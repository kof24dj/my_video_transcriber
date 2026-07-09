import os
import time
import uuid
from flask import Flask, request, render_template, jsonify
from google import genai

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_video():
    # 檢查是否有影片
    if 'video' not in request.files:
        return jsonify({'error': '沒有上傳影片'}), 400
    video_file = request.files['video']
    if video_file.filename == '':
        return jsonify({'error': '未選擇檔案'}), 400

    # ✨ 檢查是否填寫 API Key
    api_key = request.form.get('api_key')
    if not api_key:
        return jsonify({'error': '請提供 Gemini API Key'}), 400

    # 每次請求動態初始化 Client (使用當前使用者的 Key)
    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        return jsonify({'error': f'API Key 無效或初始化失敗: {str(e)}'}), 400

    # ✨ 產生獨一無二的檔名，避免同事同時上傳互相覆蓋
    _, ext = os.path.splitext(video_file.filename)
    if not ext:
        ext = '.mp4'
    unique_filename = f"temp_video_{uuid.uuid4().hex}{ext}"
    local_video_path = os.path.join(os.getcwd(), unique_filename)
    
    video_file.save(local_video_path)
    uploaded_file = None
    
    try:
        print(f"正在將影片上傳至 Gemini 伺服器...")
        uploaded_file = client.files.upload(file=local_video_path)
        
        print("等待 Gemini 處理影片中...")
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)

        if uploaded_file.state.name == "FAILED":
            raise Exception("Gemini 處理影片失敗。可能是 API 額度用盡或檔案格式不支援。")

        print("開始產生細緻時間軸的逐字稿...")
        prompt = (
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
        print(f"錯誤: {str(e)}")
        # 若發生錯誤（例如 Key 錯誤），回傳給前端顯示
        return jsonify({'error': str(e)}), 500
        
    finally:
        # 清理雲端伺服器上的本地檔案
        if os.path.exists(local_video_path):
            try: os.remove(local_video_path)
            except: pass
        # 清理 Gemini 雲端暫存檔
        if uploaded_file:
            try: client.files.delete(name=uploaded_file.name)
            except: pass

# 拿掉了 webbrowser 和 threading，雲端環境不需要這些
if __name__ == '__main__':
    # 這裡只供本地測試用，Render 會使用 gunicorn 啟動
    app.run(host='0.0.0.0', port=5000)