import os
import time
import uuid
from flask import Flask, request, render_template, jsonify
from werkzeug.exceptions import HTTPException
from google import genai
from google.genai import types  # ✨ 確保引入官方的 types 型別庫

app = Flask(__name__)

# 設定允許的最大上傳容量為 100MB
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return jsonify(error=e.description), e.code
    print(f"系統發生嚴重錯誤: {e}")
    return jsonify(error=f"伺服器內部錯誤: {str(e)}"), 500

def get_state(f):
    if not f: return "UNKNOWN"
    if hasattr(f, 'state') and f.state:
        return f.state.name if hasattr(f.state, 'name') else str(f.state)
    return "UNKNOWN"

def get_name(f):
    if not f: return ""
    if hasattr(f, 'name'): return f.name
    elif isinstance(f, dict) and 'name' in f: return f['name']
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

    _, ext = os.path.splitext(video_file.filename)
    if not ext: ext = '.mp4'
    unique_filename = f"temp_video_{uuid.uuid4().hex}{ext}"
    local_video_path = os.path.join(os.getcwd(), unique_filename)
    
    video_file.save(local_video_path)
    uploaded_file = None
    
    try:
        ext_lower = ext.lower()
        mime_type = "video/mp4"
        if ext_lower == ".mp3": mime_type = "audio/mp3"
        elif ext_lower == ".wav": mime_type = "audio/wav"
        elif ext_lower == ".m4a": mime_type = "audio/x-m4a"
        elif ext_lower == ".mov": mime_type = "video/quicktime"
        elif ext_lower == ".webm": mime_type = "video/webm"
        elif ext_lower == ".avi": mime_type = "video/x-msvideo"

        print(f"準備上傳，MIME Type: {mime_type}")
        
        # ✨【終極防彈上傳機制】: 涵蓋所有新舊版本可能的寫法
        upload_errors = []
        try:
            # 1. 官方 0.3.0 最新標準
            uploaded_file = client.files.upload(file=local_video_path, config={'mime_type': mime_type})
        except Exception as e1:
            upload_errors.append(f"寫法1: {e1}")
            try:
                # 2. 之前在你環境中成功過的 path，加上 dict config
                uploaded_file = client.files.upload(path=local_video_path, config={'mime_type': mime_type})
            except Exception as e2:
                upload_errors.append(f"寫法2: {e2}")
                try:
                    # 3. 使用嚴格的 types.UploadFileConfig 物件
                    uploaded_file = client.files.upload(path=local_video_path, config=types.UploadFileConfig(mime_type=mime_type))
                except Exception as e3:
                    upload_errors.append(f"寫法3: {e3}")
                    try:
                        # 4. 更早期的直接傳遞 mime_type 參數
                        uploaded_file = client.files.upload(path=local_video_path, mime_type=mime_type)
                    except Exception as e4:
                        upload_errors.append(f"寫法4: {e4}")
                        try:
                            # 5. 最終妥協：不帶 mime_type (至少讓你成功上傳，並交由防幻覺提示詞把關)
                            uploaded_file = client.files.upload(path=local_video_path)
                            print("⚠️ 警告：無法強制綁定 MIME Type，退回純路徑上傳。")
                        except Exception as e5:
                            upload_errors.append(f"寫法5: {e5}")
                            raise Exception(f"所有上傳模式皆失敗。詳細: {upload_errors}")
        
        print("影片已成功送達，等待處理...")
        timeout_counter = 0
        
        while get_state(uploaded_file) == "PROCESSING":
            if timeout_counter > 90:
                raise Exception("Gemini 處理影片超時。")
            time.sleep(2)
            timeout_counter += 1
            uploaded_file = client.files.get(name=get_name(uploaded_file))

        if get_state(uploaded_file) == "FAILED":
            raise Exception("雲端多媒體編碼失敗。請確認影片檔案是否損壞。")

        print(f"編碼完成！執行模式：{mode}")

        guardrail_instruction = (
            "【絕對重要防禦指令】：請務必『完全根據影片或音訊中的實際語音內容』進行聽寫與翻譯！\n"
            "絕對不可以憑空捏造、胡亂編造或自行想像任何無關的台詞或對話。\n"
            "如果你發現這個檔案完全沒有聲音，或者因為格式問題讓你聽不到任何內容，請『不要輸出任何時間軸』，直接回傳這句話：『❌ 系統偵測：檔案讀取失敗或音軌為空。請確認影片格式與聲音是否正常。』\n\n"
        )

        if mode == "translate_en_zh":
            prompt = (
                guardrail_instruction +
                "請聽這段英文語音，並直接翻譯成『繁體中文（台灣習慣用語）』字幕。\n"
                "以『單個句子』為單位精細切分時間軸，只要有停頓或換句就必須分割！\n"
                "格式：\n"
                "[HH:MM:SS - HH:MM:SS] 中文翻譯\n"
            )
        else:
            prompt = (
                guardrail_instruction +
                "請將這段影片轉成逐字稿。\n"
                "以『單個句子』為單位精細切分時間軸，只要有停頓或換句就必須分割！\n"
                "格式：\n"
                "[HH:MM:SS - HH:MM:SS] 第一句話\n"
            )

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[uploaded_file, prompt]
        )

        return jsonify({'transcript': response.text})

    except Exception as e:
        print(f"處理期間錯誤: {str(e)}")
        return jsonify({'error': str(e)}), 500
        
    finally:
        if os.path.exists(local_video_path):
            try: os.remove(local_video_path)
            except: pass
        if uploaded_file:
            try: 
                client.files.delete(name=get_name(uploaded_file))
            except: pass

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
