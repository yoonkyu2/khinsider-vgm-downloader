import os
import time
import requests
import urllib.parse
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# 📌 앨범 페이지 URL
ALBUM_URL = "https://downloads.khinsider.com/game-soundtracks/album/taiko-no-tatsujin-original-soundtrack-watagashi"

# 📌 다운로드 폴더 지정 (사용자가 원하는 경로)
DOWNLOAD_FOLDER = r"D:\Music\4. OST\MUSIC GAME OST\太鼓の達人\{CLRC-10006} Taiko no Tatsujin Original Soundtrack Watagashi [FLAC]"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# 📌 Chrome 설정 (자동 다운로드 폴더 지정)
chrome_options = Options()
chrome_options.add_argument("--headless")  # 백그라운드 실행
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--log-level=3")

# 📌 웹 드라이버 실행
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=chrome_options)

# 1️⃣ **앨범 페이지에서 MP3 다운로드 페이지 URL 가져오기 (중복 제거)**
driver.get(ALBUM_URL)
time.sleep(2)  # 페이지 로딩 대기

soup = BeautifulSoup(driver.page_source, "html.parser")
track_links = set()  # 중복 방지를 위해 set 사용

# ✅ MP3 다운로드 페이지 링크 가져오기 (중복 제거)
for a in soup.select("a"):
    href = a.get("href", "")
    if href.endswith(".mp3"):  # MP3 다운로드 페이지인지 확인
        track_links.add("https://downloads.khinsider.com" + href)

track_links = list(track_links)  # set을 list로 변환
print(f"🔍 중복 제거 후 {len(track_links)}개의 트랙을 찾았습니다.")

# 2️⃣ **각 MP3 다운로드 페이지에서 FLAC 직접 다운로드**
for idx, track_url in enumerate(track_links):
    driver.get(track_url)
    time.sleep(1)

    # MP3 다운로드 페이지에서 FLAC 다운로드 링크 찾기
    soup = BeautifulSoup(driver.page_source, "html.parser")
    flac_link = None

    for a in soup.select("a"):
        if a.get("href", "").endswith(".flac"):  # .flac으로 끝나는 링크 찾기
            flac_link = a["href"]
            break

    if not flac_link:
        print(f"[{idx+1}/{len(track_links)}] {track_url} - FLAC 파일 없음")
        continue

    # ✅ URL 디코딩하여 파일명 정리
    file_name = os.path.basename(flac_link)
    file_name = urllib.parse.unquote(file_name)  # URL 인코딩된 문자 변환
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    
    print(f"[{idx+1}/{len(track_links)}] FLAC 다운로드 중: {file_name}")

    with requests.get(flac_link, stream=True) as r:
        with open(file_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

print("✅ 모든 다운로드가 완료되었습니다!")
driver.quit()
