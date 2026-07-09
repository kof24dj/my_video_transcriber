import os
import time
import uuid
from flask import Flask, request, render_template, jsonify
from werkzeug.exceptions import HTTPException
import google.generativeai as genai

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return jsonify(error=e.description), e.code
    print(f"系統發生嚴重錯誤: {e}")
    return jsonify(error=f"伺服器內部錯誤: {str(e)}"), 500

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
        genai.configure(api_key=api_key)
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

        print(f"準備上傳，暴力綁定 MIME Type: {mime_type}")
        
        uploaded_file = genai.upload_file(path=local_video_path, mime_type=mime_type)
        
        print("影片已成功送達，等待處理...")
        timeout_counter = 0
        
        while uploaded_file.state.name == "PROCESSING":
            if timeout_counter > 90:
                raise Exception("Gemini 處理影片超時。")
            time.sleep(2)
            timeout_counter += 1
            uploaded_file = genai.get_file(uploaded_file.name)

        if uploaded_file.state.name == "FAILED":
            raise Exception("雲端多媒體編碼失敗。請確認影片檔案是否損壞。")

        print(f"編碼完成！執行模式：{mode}")

        guardrail_instruction = (
            "【絕對重要防禦指令】：請務必『完全根據影片或音訊中的實際語音內容』進行聽寫與翻譯！\n"
            "絕對不可以憑空捏造、胡亂編造或自行想像任何無關的台詞或對話。\n"
            "如果你發現這個檔案完全沒有聲音，或者因為格式問題讓你聽不到任何內容，請『不要輸出任何時間軸』，直接回傳這句話：『❌ 系統偵測：檔案讀取失敗或音軌為空。請確認影片格式與聲音是否正常。』\n\n"
        )

        # ✨【核心修正：加入極短句限制與強制字數規定】
        if mode == "translate_en_zh":
            prompt = (
                guardrail_instruction +
                "請聽這段英文語音，並直接翻譯成『繁體中文（台灣習慣用語）』字幕。\n"
                "【極短句強制切分】：為了讓字幕在畫面上好閱讀，每行中文字幕『絕對不可超過 15 個字』！\n"
                "遇到較長的句子時，請務必根據語氣、逗號、或講話的微小停頓，將其強制切斷為多個超短句，並分別給予獨立的時間軸。\n"
                "格式：\n"
                "[HH:MM:SS - HH:MM:SS] 短句中文翻譯\n"
            )
        else:
            prompt = (
                guardrail_instruction +
                "請將這段影片轉成逐字稿。\n"
                "【極短句強制切分】：為了讓字幕在畫面上好閱讀，每行字幕『絕對不可超過 15 個字』！\n"
                "遇到較長的句子時，請務必根據語氣、逗號、或講話的微小停頓，將其強制切斷為多個超短句，並分別給予獨立的時間軸。\n"
                "格式：\n"
                "[HH:MM:SS - HH:MM:SS] 超短句話語\n"
            )

        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content([uploaded_file, prompt])

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
                genai.delete_file(uploaded_file.name)
            except: pass

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
