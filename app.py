import os
import time
import uuid
from flask import Flask, request, render_template, jsonify
from werkzeug.exceptions import HTTPException
from google import genai

app = Flask(__name__)

# 設定允許的最大上傳容量為 100MB
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

# 萬能防彈機制：攔截所有伺服器錯誤，強制回傳 JSON 格式，避免前端解析失敗
@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return jsonify(error=e.description), e.code
    print(f"系統發生嚴重錯誤: {e}")
    return jsonify(error=f"伺服器內部錯誤: {str(e)}"), 500

# 輔助函數：相容新舊版 SDK 的狀態讀取
def get_state(f):
    if not f:
        return "UNKNOWN"
    if hasattr(f, 'state') and f.state:
        return f.state.name if hasattr(f.state, 'name') else str(f.state)
    return "UNKNOWN"

# 輔助函數：相容新舊版 SDK 的檔案名稱讀取
def get_name(f):
    if not f:
        return ""
    if hasattr(f, 'name'):
        return f.name
    elif isinstance(f, dict) and 'name' in f:
        return f['name']
    return str(f)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_video():
    if 'video' not in request.files:
        return jsonify({'error': '沒有上傳影片'}), 400
    video_file = request.files['video']
    if video_file.filename == '':
        return jsonify({'error': '未選擇檔案'}), 400

    api_key = request.form.get('api_key')
    if not api_key:
        return jsonify({'error': '請提供 Gemini API Key'}), 400

    mode = request.form.get('mode', 'transcribe')

    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        return jsonify({'error': f'API Key 初始化失敗: {str(e)}'}), 400

    # 產生不重複檔名，避免多人同時使用互相覆蓋
    _, ext = os.path.splitext(video_file.filename)
    if not ext:
        ext = '.mp4'
    unique_filename = f"temp_video_{uuid.uuid4().hex}{ext}"
    local_video_path = os.path.join(os.getcwd(), unique_filename)
    
    video_file.save(local_video_path)
    uploaded_file = None
    
    try:
        # ✨【核心修正：解決雲端環境 MIME Type 誤判導致 AI 瞎編字幕的問題】
        ext_lower = ext.lower()
        mime_type = "video/mp4"  # 預設為 MP4 影片
        if ext_lower == ".mp3": mime_type = "audio/mp3"
        elif ext_lower == ".wav": mime_type = "audio/wav"
        elif ext_lower == ".m4a": mime_type = "audio/x-m4a"
        elif ext_lower == ".mov": mime_type = "video/quicktime"
        elif ext_lower == ".webm": mime_type = "video/webm"
        elif ext_lower == ".avi": mime_type = "video/x-msvideo"

        print(f"本地端暫存成功。準備上傳至 Gemini 雲端，強制指定 MIME Type: {mime_type}")
        
        # 為了相容不同版本的 Google SDK 參數命名，使用多重嘗試機制把 mime_type 灌進去
        upload_errors = []
        try:
            uploaded_file = client.files.upload(file=local_video_path, config={'mime_type': mime_type})
        except Exception as e1:
            upload_errors.append(f"寫法1失敗: {e1}")
            try:
                uploaded_file = client.files.upload(file=local_video_path, mime_type=mime_type)
            except Exception as e2:
                upload_errors.append(f"寫法2失敗: {e2}")
                try:
                    uploaded_file = client.files.upload(local_video_path, mime_type=mime_type)
                except Exception as e3:
                    upload_errors.append(f"寫法3失敗: {e3}")
                    try:
                        uploaded_file = client.files.upload(local_video_path)
                    except Exception as e4:
                        upload_errors.append(f"寫法4失敗: {e4}")
                        raise Exception(f"所有雲端上傳相容模式皆失敗。詳細紀錄: {upload_errors}")
        
        print("影片已成功送達 Google 伺服器，等待 Gemini 進行多媒體編碼處理...")
        timeout_counter = 0
        
        # 循環檢查處理狀態是否為 PROCESSING
        while get_state(uploaded_file) == "PROCESSING":
            if timeout_counter > 90:  # 最多等待約 3 分鐘
                raise Exception("Gemini 處理影片超時，請稍後再試。")
            time.sleep(2)
            timeout_counter += 1
            uploaded_file = client.files.get(name=get_name(uploaded_file))

        if get_state(uploaded_file) == "FAILED":
            raise Exception("Gemini 雲端多媒體編碼失敗。請確認影片檔案是否損壞，或音訊編碼是否為常見格式。")

        print(f"雲端編碼完成！正在執行模式：{mode}，開始由 Gemini 產生字幕...")

        # ✨【核心修正：加入幻覺防禦指令，避免沒讀到音軌時瞎編故事】
        guardrail_instruction = (
            "【絕對重要防禦指令】：請務必『完全根據影片或音訊中的實際語音內容』進行聽寫與翻譯！\n"
            "絕對不可以憑空捏造、胡亂編造或自行想像任何無關的台詞或對話。\n"
            "如果發現該影片完全沒有聲音、或者你無法讀取其音軌內容，請『不要輸出任何時間軸』，直接回傳這行字：『❌ 錯誤：無法從該檔案中讀取到任何有效音訊內容，請檢查檔案編碼是否為 H.264/AAC 標準格式。』\n\n"
        )

        if mode == "translate_en_zh":
            prompt = (
                guardrail_instruction +
                "請幫我聽這段影片的英文語音，並將其【直接翻譯成繁體中文（台灣習慣用語）】字幕。\n"
                "請以『單個句子』為單位進行極其精確、細緻的時間軸切分。\n"
                "【嚴格要求】：只要說話者有短暫停頓、換句、或是語意轉換，就必須分割成新的時間戳記！"
                "絕對不允許將 2-3 句話合併在同一個時間段內。請確保中文字幕流暢自然且符合台灣人說話邏輯。\n\n"
                "請嚴格按照以下格式輸出，時間務必補足『時:分:秒』（例如 00:00:15），不要包含任何額外的解釋、引導文字或 Markdown 標記：\n"
                "[HH:MM:SS - HH:MM:SS] 第一句中文翻譯\n"
                "[HH:MM:SS - HH:MM:SS] 第二句中文翻譯"
            )
        else:
            prompt = (
                guardrail_instruction +
                "請幫我將這段影片轉成逐字稿。請以『單個句子』為單位進行極其精確、細緻的時間軸切分。\n"
                "只要說話者有短暫停頓、換句、或是語意轉換，就必須分割成新的時間戳記！"
                "絕對不允許將 2-3 句話合併在同一個時間段內。\n\n"
                "請嚴格按照以下格式輸出，時間務必補足『時:分:秒』（例如 00:00:15），不要包含任何額外的解釋、引導文字或 Markdown 標記：\n"
                "[HH:MM:SS - HH:MM:SS] 第一句話\n"
                "[HH:MM:SS - HH:MM:SS] 第二句話"
            )

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[uploaded_file, prompt]
        )

        return jsonify({'transcript': response.text})

    except Exception as e:
        print(f"處理期間發生錯誤: {str(e)}")
        return jsonify({'error': str(e)}), 500
        
    finally:
        # 清理本地暫存檔
        if os.path.exists(local_video_path):
            try: os.remove(local_video_path)
            except: pass
        # 清理 Gemini 雲端暫存檔
        if uploaded_file:
            try: 
                client.files.delete(name=get_name(uploaded_file))
                print("已成功刪除 Gemini 雲端暫存檔案")
            except Exception as delete_error:
                print(f"刪除雲端檔案時失敗: {delete_error}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
