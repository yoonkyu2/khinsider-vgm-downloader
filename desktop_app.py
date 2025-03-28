import os
import time
import json
import requests
import urllib.parse
import threading
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
import re
from datetime import datetime

class SafeChrome(uc.Chrome):
    """안전한 Chrome 드라이버 클래스"""
    def __del__(self):
        """소멸자에서 quit() 호출하지 않음"""
        pass

class DownloaderThread(threading.Thread):
    def __init__(self, album_url, download_folder, progress_callback):
        super().__init__()
        self.album_url = album_url
        self.download_folder = download_folder
        self.progress_callback = progress_callback
        self.is_running = True
        self.driver = None
        self._driver_lock = threading.Lock()
        self._cleanup_event = threading.Event()
        self._is_driver_quit = False
        self._is_cleaning_up = False
        self._driver_options = None
        print(f"\n=== DownloaderThread 생성: {id(self)} ===")

    def _create_driver_options(self):
        """Chrome 옵션 생성"""
        print(f"=== Chrome 옵션 생성: {id(self)} ===")
        options = uc.ChromeOptions()
        options.add_argument('--headless')
        options.add_argument('--disable-gpu')
        options.add_argument('--log-level=3')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-browser-side-navigation')
        options.add_argument('--disable-infobars')
        options.add_argument('--disable-extensions')
        options.add_argument('--disable-software-rasterizer')
        options.add_argument('--disable-dev-tools')
        options.add_argument('--disable-notifications')
        options.add_argument('--disable-popup-blocking')
        options.add_argument('--disable-save-password-bubble')
        options.add_argument('--disable-translate')
        options.add_argument('--disable-web-security')
        options.add_argument('--disable-features=IsolateOrigins,site-per-process')
        options.add_argument('--disable-site-isolation-trials')
        return options

    def sanitize_filename(self, filename):
        # 윈도우에서 사용할 수 없는 특수문자 제거
        # 파일/폴더명으로 사용할 수 없는 문자: \ / : * ? " < > |
        sanitized = re.sub(r'[\\/:*?"<>|]', '_', filename)
        # 연속된 공백과 언더스코어를 하나로 치환
        sanitized = re.sub(r'[\s_]+', ' ', sanitized)
        # 앞뒤 공백 제거
        sanitized = sanitized.strip()
        # 빈 문자열이면 기본값 사용
        if not sanitized:
            sanitized = "album"
        return sanitized

    def download_file(self, url, file_path):
        response = requests.get(url, stream=True)
        total_size = int(response.headers.get('content-length', 0))
        block_size = 8192
        downloaded = 0
        
        file_name = os.path.basename(file_path)
        self.progress_callback(f"file_status:{file_name}:다운로드 중")
        
        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=block_size):
                if not self.is_running:
                    self.progress_callback(f"file_status:{file_name}:중단됨")
                    return False
                if chunk:
                    downloaded += len(chunk)
                    f.write(chunk)
                    if total_size:
                        progress = (downloaded / total_size) * 100
                        self.progress_callback(f"progress:{progress:.1f}")
        
        self.progress_callback(f"file_status:{file_name}:완료")
        return True

    def create_subfolder(self, base_folder, subfolder_name):
        # 폴더명 정리
        safe_name = self.sanitize_filename(subfolder_name)
        folder_path = os.path.join(base_folder, safe_name)
        os.makedirs(folder_path, exist_ok=True)
        return folder_path

    def download_images(self, soup, base_folder):
        # 이미지 다운로드를 위한 하위 폴더 생성
        images_folder = self.create_subfolder(base_folder, "Scans")
        
        # 앨범 커버 이미지 영역 찾기
        # 1. h2 태그 (앨범 제목) 찾기
        h2_element = soup.find('h2')
        if not h2_element:
            self.progress_callback("⚠️ 앨범 제목을 찾을 수 없습니다.")
            return []
            
        # 2. h2 다음에 나오는 테이블들 중 단일 셀을 가진 테이블 찾기
        current = h2_element
        album_table = None
        
        while current:
            if current.name == 'table':
                # audio player 테이블인지 확인 (플레이어는 항상 이미지 테이블 다음에 옴)
                audio_player = current.find('audio')
                if audio_player:
                    break
                    
                # 단일 셀을 가진 테이블 찾기
                cells = current.find_all('td')
                if len(cells) == 1:  # 하나의 셀만 있는 테이블
                    album_table = current
                    break
            current = current.find_next()
            
        if not album_table:
            self.progress_callback("⚠️ 앨범 커버 테이블을 찾을 수 없습니다.")
            return []
            
        # 3. 테이블 내의 빈 텍스트 링크 찾기
        image_links = []
        for a in album_table.find_all('a'):
            # 빈 텍스트를 가진 링크만 선택 (공백이나 대괄호만 있는 경우도 포함)
            if a.text.strip().replace('[', '').replace(']', '').strip() == '':
                href = a.get('href', '')
                if any(ext in href.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.png']):
                    image_links.append(href)
    
        if image_links:
            self.progress_callback(f"🖼️ {len(image_links)}개의 앨범 커버 이미지를 찾았습니다.")
            
            # 이미지 순서대로 정렬 (파일명 기준)
            image_links.sort(key=lambda x: os.path.basename(x))
            
            # 전체 파일 개수 계산 (이미지만)
            total_files = len(image_links)
            current_file = 0
            
            for idx, img_url in enumerate(image_links, 1):
                if not self.is_running:
                    self.driver.quit()
                    return image_links

                current_file += 1
                total_progress = (current_file / total_files) * 100
                self.progress_callback(f"total_progress:{total_progress:.1f}")

                file_name = os.path.basename(urllib.parse.unquote(img_url.split('?')[0]))
                file_path = os.path.join(images_folder, file_name)
                
                self.progress_callback(f"file_status:{file_name}:대기 중")
                
                try:
                    if self.download_file(img_url, file_path):
                        self.progress_callback(f"✅ 이미지 저장 완료: {file_name}")
                        current_file += 1
                        total_progress = (current_file / total_files) * 100
                        self.progress_callback(f"total_progress:{total_progress:.1f}")
                    else:
                        self.progress_callback(f"file_status:{file_name}:중단됨")
                except Exception as e:
                    self.progress_callback(f"file_status:{file_name}:실패")
                    self.progress_callback(f"❌ 이미지 다운로드 실패: {file_name} - {str(e)}")
        else:
            self.progress_callback("ℹ️ 다운로드할 이미지를 찾지 못했습니다.")
            
        return image_links

    def quit_driver(self):
        """드라이버를 안전하게 종료하는 메서드"""
        if self._is_cleaning_up:
            print(f"=== 드라이버 정리 중복 방지: {id(self)} ===")
            return
            
        with self._driver_lock:
            if self.driver and not self._is_driver_quit:
                self._is_cleaning_up = True
                try:
                    print(f"=== 드라이버 종료 시작: {id(self)} ===")
                    # 드라이버 종료 전에 모든 탭 닫기
                    if hasattr(self.driver, 'window_handles'):
                        for handle in self.driver.window_handles:
                            try:
                                self.driver.switch_to.window(handle)
                                self.driver.close()
                            except:
                                pass
                    
                    # 드라이버 종료
                    if hasattr(self.driver, 'quit'):
                        try:
                            self.progress_callback("🔄 Chrome 드라이버 종료 중...")
                            # 드라이버의 내부 상태 초기화
                            if hasattr(self.driver, '_driver'):
                                self.driver._driver = None
                            if hasattr(self.driver, '_service'):
                                self.driver._service = None
                            self.driver.quit()
                            self.progress_callback("✅ Chrome 드라이버 종료 완료")
                            print(f"=== 드라이버 quit() 완료: {id(self)} ===")
                        except:
                            self.progress_callback("⚠️ Chrome 드라이버 종료 중 오류 발생")
                            print(f"=== 드라이버 quit() 실패: {id(self)} ===")
                    self._is_driver_quit = True
                except Exception as e:
                    self.progress_callback(f"❌ 드라이버 종료 중 오류 발생: {str(e)}")
                    print(f"=== 드라이버 종료 중 예외 발생: {id(self)} - {str(e)} ===")
                finally:
                    # 드라이버 객체의 모든 참조 제거
                    print(f"=== 드라이버 참조 제거: {id(self)} ===")
                    self.driver = None
                    self._driver_options = None
                    self._cleanup_event.set()
                    self._is_cleaning_up = False
                    # 가비지 컬렉션 유도
                    import gc
                    gc.collect()
                    print(f"=== 가비지 컬렉션 완료: {id(self)} ===")

    def stop(self):
        """다운로드를 중지하고 리소스를 정리하는 메서드"""
        if not self.is_running:
            return
            
        self.is_running = False
        self.quit_driver()
        
        try:
            self._cleanup_event.wait(timeout=2.0)
        except Exception as e:
            print(f"정리 대기 중 오류 발생: {str(e)}")

    def __del__(self):
        """객체 소멸 시 리소스 정리"""
        print(f"\n=== DownloaderThread 소멸 시작: {id(self)} ===")
        if not self._is_cleaning_up:
            print(f"=== DownloaderThread stop() 호출: {id(self)} ===")
            self.stop()
        print(f"=== DownloaderThread 소멸 완료: {id(self)} ===\n")

    def run(self):
        try:
            print(f"\n=== DownloaderThread 실행 시작: {id(self)} ===")
            # Chrome 옵션 생성
            self._driver_options = self._create_driver_options()

            # 웹 드라이버 실행
            with self._driver_lock:
                if not self._is_driver_quit and not self._is_cleaning_up:
                    try:
                        print(f"=== Chrome 드라이버 생성 시도: {id(self)} ===")
                        self.driver = SafeChrome(options=self._driver_options)
                        print(f"=== Chrome 드라이버 생성 완료: {id(self)} ===")
                        self._cleanup_event.clear()
                    except Exception as e:
                        print(f"=== Chrome 드라이버 생성 실패: {id(self)} - {str(e)} ===")
                        return

            if not self.driver:
                return

            # 앨범 페이지 접속
            self.driver.get(self.album_url)
            time.sleep(2)

            # 페이지 파싱
            soup = BeautifulSoup(self.driver.page_source, "html.parser")
            
            # 앨범 제목과 카탈로그 번호 추출
            album_title = soup.select_one("h2")
            if not album_title:
                self.progress_callback("⚠️ 앨범 제목을 찾을 수 없습니다.")
                self.driver.quit()
                return

            album_name = album_title.text.strip()
            self.progress_callback(f"💿 앨범 제목: {album_name}")
            
            # 카탈로그 번호 찾기
            catalog_text = None
            for text in soup.stripped_strings:
                if "Catalog Number:" in text:
                    element = soup.find(string=lambda t: t and "Catalog Number:" in t)
                    if element:
                        next_b = element.find_next('b')
                        if next_b:
                            catalog_text = next_b.text.strip()
                        break

            # 이미지 링크 수집
            image_links = []
            current = album_title
            album_table = None
            
            while current:
                if current.name == 'table':
                    audio_player = current.find('audio')
                    if audio_player:
                        break
                    cells = current.find_all('td')
                    if len(cells) == 1:
                        album_table = current
                        break
                current = current.find_next()
                
            if album_table:
                for a in album_table.find_all('a'):
                    if a.text.strip().replace('[', '').replace(']', '').strip() == '':
                        href = a.get('href', '')
                        if any(ext in href.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.png']):
                            image_links.append(href)
                image_links.sort(key=lambda x: os.path.basename(x))

            # 음원 링크 수집 및 형식 확인
            track_links = set()
            for a in soup.select("a"):
                href = a.get("href", "")
                if href.endswith(".mp3"):
                    track_links.add("https://downloads.khinsider.com" + href)
            track_links = list(track_links)

            # 첫 번째 트랙으로 FLAC 가용성 확인
            file_type = "MP3"  # 기본값
            if track_links:
                self.driver.get(track_links[0])
                time.sleep(1)
                track_soup = BeautifulSoup(self.driver.page_source, "html.parser")
                for a in track_soup.select("a"):
                    if a.get("href", "").endswith(".flac"):
                        file_type = "FLAC"
                        break

            # 앨범 폴더 생성
            if catalog_text:
                self.progress_callback(f"📀 카탈로그 번호: {catalog_text}")
                folder_name = f"{{{catalog_text}}} {album_name} [{file_type}]"
            else:
                self.progress_callback("⚠️ 카탈로그 번호를 찾을 수 없습니다.")
                folder_name = f"{album_name} [{file_type}]"

            album_folder = self.create_subfolder(self.download_folder, folder_name)
            self.progress_callback(f"📁 저장 폴더: {os.path.basename(album_folder)}")

            # 전체 파일 개수 계산
            total_files = len(image_links) + len(track_links)
            current_file = 0
            
            # 진행 상황 출력
            self.progress_callback(f"\n🖼️ {len(image_links)}개의 앨범 커버 이미지를 찾았습니다.")
            self.progress_callback(f"🔍 총 {len(track_links)}개의 {file_type} 트랙을 찾았습니다.")
            self.progress_callback(f"📥 총 {total_files}개 파일 다운로드를 시작합니다...\n")
            self.progress_callback(f"total_files:{total_files}")  # 전체 파일 수 보고

            # 이미지 다운로드
            if image_links:
                images_folder = self.create_subfolder(album_folder, "Scans")
                for idx, img_url in enumerate(image_links, 1):
                    if not self.is_running:
                        self.driver.quit()
                        return

                    file_name = os.path.basename(urllib.parse.unquote(img_url.split('?')[0]))
                    file_path = os.path.join(images_folder, file_name)
                    
                    self.progress_callback(f"file_status:{file_name}:대기 중")
                    
                    try:
                        if self.download_file(img_url, file_path):
                            current_file += 1
                            total_progress = (current_file / total_files) * 100
                            self.progress_callback(f"total_progress:{total_progress:.1f}")
                    except Exception as e:
                        self.progress_callback(f"file_status:{file_name}:실패")
                        self.progress_callback(f"❌ 이미지 다운로드 실패: {file_name} - {str(e)}")

            # 음원 다운로드
            for idx, track_url in enumerate(track_links, 1):
                if not self.is_running:
                    self.driver.quit()
                    return

                self.driver.get(track_url)
                time.sleep(1)

                track_soup = BeautifulSoup(self.driver.page_source, "html.parser")
                download_link = None

                # 설정된 형식(FLAC/MP3)에 따라 다운로드 링크 찾기
                for a in track_soup.select("a"):
                    href = a.get("href", "")
                    if file_type == "FLAC" and href.endswith(".flac"):
                        download_link = href
                        break
                    elif file_type == "MP3" and href.endswith(".mp3"):
                        download_link = href
                        break

                if not download_link:
                    self.progress_callback(f"file_status:트랙 {idx}:실패")
                    self.progress_callback(f"[{idx}/{len(track_links)}] ❌ 다운로드 가능한 파일을 찾을 수 없습니다.")
                    continue

                file_name = os.path.basename(download_link)
                file_name = urllib.parse.unquote(file_name)
                file_path = os.path.join(album_folder, file_name)
                
                self.progress_callback(f"file_status:{file_name}:대기 중")

                if self.download_file(download_link, file_path):
                    current_file += 1
                    total_progress = (current_file / total_files) * 100
                    self.progress_callback(f"total_progress:{total_progress:.1f}")
                else:
                    self.progress_callback(f"file_status:{file_name}:중단됨")

            self.progress_callback("total_progress:100.0")
            self.progress_callback("\n✨ 모든 다운로드가 완료되었습니다!")

        except Exception as e:
            self.progress_callback(f"❌ 오류 발생: {str(e)}")
            print(f"=== DownloaderThread 실행 중 예외 발생: {id(self)} - {str(e)} ===")
        finally:
            print(f"=== DownloaderThread 실행 종료: {id(self)} ===")
            self.quit_driver()
            try:
                self._cleanup_event.wait(timeout=2.0)
                print(f"=== 정리 이벤트 대기 완료: {id(self)} ===")
            except Exception as e:
                print(f"=== 정리 이벤트 대기 실패: {id(self)} - {str(e)} ===")

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("KHInsider Downloader")
        self.root.geometry("1200x800")
        
        # 프로그램 상태 변수
        self.is_closing = False
        self.current_download = None
        self.queue_info = {}
        self.download_queue = []
        self.state_file = "downloader_state.json"
        
        # 종료 이벤트 처리 추가
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # 스타일 설정
        style = ttk.Style()
        style.configure('TButton', padding=5)
        style.configure('TEntry', padding=5)
        
        # 메인 프레임
        main_frame = ttk.Frame(root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        # URL 입력과 다운로드 시작 버튼
        url_frame = ttk.Frame(main_frame)
        url_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=5)
        ttk.Label(url_frame, text="앨범 URL:").pack(side=tk.LEFT)
        self.url_entry = ttk.Entry(url_frame)
        self.url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))
        self.start_button = ttk.Button(url_frame, text="다운로드 시작", command=self.start_download)
        self.start_button.pack(side=tk.LEFT, padx=(0, 5))

        # 다운로드 폴더 선택
        folder_frame = ttk.Frame(main_frame)
        folder_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=5)
        ttk.Label(folder_frame, text="저장 위치:").pack(side=tk.LEFT)
        self.folder_entry = ttk.Entry(folder_frame)
        self.folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))
        ttk.Button(folder_frame, text="폴더 선택", command=self.select_folder).pack(side=tk.LEFT)

        # 대기열 프레임
        queue_frame = ttk.LabelFrame(main_frame, text="앨범 다운 대기열", padding="5")
        queue_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=5)
        queue_frame.columnconfigure(0, weight=1)
        queue_frame.rowconfigure(0, weight=1)  # 트리뷰가 세로로 늘어날 수 있도록

        # 대기열 트리뷰
        self.queue_tree = ttk.Treeview(queue_frame, columns=('album', 'progress_text'), height=5)
        self.queue_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # 대기열 스크롤바
        queue_scrollbar = ttk.Scrollbar(queue_frame, orient="vertical", command=self.queue_tree.yview)
        queue_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.queue_tree.configure(yscrollcommand=queue_scrollbar.set)
        
        # 대기열 트리뷰 설정
        self.queue_tree.heading('album', text='앨범 다운 대기열')
        self.queue_tree.heading('progress_text', text='진행상황')
        self.queue_tree.column('album', width=800, anchor='w')
        self.queue_tree.column('progress_text', width=200, anchor='center')
        self.queue_tree.column('#0', width=0, stretch=False)

        # 로그와 파일 목록을 포함하는 프레임
        content_frame = ttk.Frame(main_frame)
        content_frame.grid(row=3, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        content_frame.columnconfigure(0, weight=2)  # 로그에 더 많은 공간
        content_frame.columnconfigure(1, weight=1)  # 파일 목록
        content_frame.rowconfigure(0, weight=1)

        # 로그 출력 (왼쪽)
        log_frame = ttk.LabelFrame(content_frame, text="다운로드 진행 상황", padding="5")
        log_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(0, 5))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        # 로그 텍스트
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # 파일 목록 (오른쪽)
        files_frame = ttk.LabelFrame(content_frame, text="파일 목록", padding="5")
        files_frame.grid(row=0, column=1, sticky=(tk.W, tk.E, tk.N, tk.S))
        files_frame.columnconfigure(0, weight=1)
        files_frame.rowconfigure(0, weight=1)

        # 파일 목록 트리뷰
        self.tree = ttk.Treeview(files_frame, columns=('filename', 'status'))
        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # 파일 목록 트리뷰 설정
        self.tree.heading('filename', text='파일명')
        self.tree.heading('status', text='상태')
        self.tree.column('filename', width=250, anchor='w')
        self.tree.column('status', width=100, anchor='center')
        self.tree.column('#0', width=0, stretch=False)

        # 파일 목록 스크롤바
        files_scrollbar = ttk.Scrollbar(files_frame, orient="vertical", command=self.tree.yview)
        files_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.tree.configure(yscrollcommand=files_scrollbar.set)

        # 진행 상태 바 프레임
        progress_frame = ttk.LabelFrame(main_frame, text="진행 상황", padding="5")
        progress_frame.grid(row=4, column=0, sticky=(tk.W, tk.E), pady=5)
        
        # 현재 파일 진행 상태
        current_frame = ttk.Frame(progress_frame)
        current_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 5))
        ttk.Label(current_frame, text="현재 파일:").grid(row=0, column=0, sticky=tk.W)
        self.current_progress = ttk.Progressbar(current_frame, mode='determinate', maximum=100)
        self.current_progress.grid(row=1, column=0, sticky=(tk.W, tk.E))
        
        # 전체 진행 상태
        total_frame = ttk.Frame(progress_frame)
        total_frame.grid(row=1, column=0, sticky=(tk.W, tk.E))
        ttk.Label(total_frame, text="전체 진행:").grid(row=0, column=0, sticky=tk.W)
        self.total_progress = ttk.Progressbar(total_frame, mode='determinate', maximum=100)
        self.total_progress.grid(row=1, column=0, sticky=(tk.W, tk.E))
        
        # 프레임 크기 조절 설정
        progress_frame.columnconfigure(0, weight=1)
        current_frame.columnconfigure(0, weight=1)
        total_frame.columnconfigure(0, weight=1)

        # 중지 버튼
        self.stop_button = ttk.Button(progress_frame, text="중지", command=self.stop_download, state=tk.DISABLED)
        self.stop_button.grid(row=2, column=0, sticky=tk.E, pady=(5, 0))

        # 다운로더 스레드
        self.downloader = None

        # 창 크기 조절 가능하도록 설정
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(3, weight=1)

        # 저장된 상태 불러오기
        self.load_state()

    def select_folder(self):
        folder = filedialog.askdirectory(title="다운로드 폴더 선택")
        if folder:
            self.folder_entry.delete(0, tk.END)
            self.folder_entry.insert(0, folder)

    def update_log(self, message):
        if message.startswith("progress:"):
            progress = float(message.split(":")[1])
            self.current_progress["value"] = progress
        elif message.startswith("total_progress:"):
            progress = float(message.split(":")[1])
            self.total_progress["value"] = progress
            # 현재 다운로드 중인 항목 찾아서 진행률 업데이트
            for item in self.queue_tree.get_children():
                if self.queue_info[item]['status'] == 'downloading':
                    total_files = self.queue_info[item].get('total_files', 0)
                    current_files = int(total_files * progress / 100)
                    self.queue_tree.set(item, 'progress_text', 
                        f"{progress:.1f}% [{current_files}/{total_files}]")
                    break
        elif message.startswith("file_status:"):
            # file_status:파일명:상태 형식으로 메시지 처리
            _, filename, status = message.split(":", 2)
            self.update_file_status(filename, status)
        elif message.startswith("total_files:"):
            # 전체 파일 수 저장 (기존 정보 유지)
            _, total = message.split(":")
            for item in self.queue_tree.get_children():
                if self.queue_info[item]['status'] == 'downloading':
                    self.queue_info[item].update({'total_files': int(total)})
                    break
        else:
            # 새로운 다운로드 시작 시 구분선 추가
            if message.startswith("💿 앨범 제목:") and self.log_text.get("1.0", tk.END).strip():
                self.log_text.insert(tk.END, "\n" + "-" * 80 + "\n\n")
            # 앨범 정보는 로그 텍스트에 표시
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
            
            # 앨범 제목을 발견하면 대기열 항목 업데이트
            if message.startswith("💿 앨범 제목:"):
                album_name = message.replace("💿 앨범 제목: ", "").strip()
                for item in self.queue_tree.get_children():
                    if self.queue_info[item]['status'] == 'downloading':
                        self.queue_tree.set(item, 'album', album_name)
                        break
            # 폴더명이 결정되면 대기열 항목 최종 업데이트
            elif message.startswith("📁 저장 폴더:"):
                folder_name = message.replace("📁 저장 폴더: ", "").strip()
                for item in self.queue_tree.get_children():
                    if self.queue_info[item]['status'] == 'downloading':
                        self.queue_tree.set(item, 'album', folder_name)
                        break
        
        self.root.update_idletasks()

    def update_file_status(self, filename, status):
        # 파일이 이미 트리뷰에 있는지 확인
        for item in self.tree.get_children():
            if self.tree.item(item)['values'][0] == filename:
                self.tree.set(item, 'status', status)
                return
        
        # 새로운 파일 추가
        self.tree.insert('', 'end', values=(filename, status))
        self.tree.see(self.tree.get_children()[-1])

    def start_download(self):
        if self.is_closing:  # 종료 중이면 새로운 다운로드 시작 안 함
            return
            
        album_url = self.url_entry.get().strip()
        download_folder = self.folder_entry.get().strip()

        if not album_url or not download_folder:
            self.update_log("❌ URL과 다운로드 폴더를 모두 입력해주세요.")
            return

        try:
            os.makedirs(download_folder, exist_ok=True)
        except Exception as e:
            self.update_log(f"❌ 폴더 생성 실패: {str(e)}")
            return

        # URL에서 앨범명 추출
        album_name = album_url.split("/album/")[-1].strip("/")
        if not album_name:
            album_name = "다운로드 대기 중..."

        # 대기열에 추가
        item_id = self.queue_tree.insert('', 'end', values=(album_name, "대기 중"))
        self.queue_info[item_id] = {
            'total_files': 0,
            'url': album_url,
            'folder': download_folder,
            'status': 'waiting'  # 상태 추가: waiting, downloading, completed, stopped
        }
        
        # 현재 다운로드가 없으면 시작
        if not self.current_download:
            self.process_next_download()
        
        # URL 입력창 초기화
        self.url_entry.delete(0, tk.END)

    def process_next_download(self):
        # 대기 중인 항목 찾기
        next_item = None
        for item in self.queue_tree.get_children():
            if self.queue_info[item]['status'] == 'waiting':
                next_item = item
                break

        if next_item:
            # 현재 항목 정보 가져오기
            item_info = self.queue_info[next_item]
            album_url = item_info['url']
            download_folder = item_info['folder']
            
            # 상태 업데이트
            item_info['status'] = 'downloading'
            self.queue_tree.set(next_item, 'progress_text', "0% [0/0]")
            
            self.stop_button.config(state=tk.NORMAL)
            self.current_progress["value"] = 0
            self.total_progress["value"] = 0

            self.current_download = DownloaderThread(album_url, download_folder, self.update_log)
            self.current_download.daemon = True
            self.current_download.start()

            # 다운로드 완료 체크
            self.check_download_status()

    def check_download_status(self):
        if self.current_download and self.current_download.is_alive():
            self.root.after(100, self.check_download_status)
        else:
            # 다운로드 완료 시
            if self.current_download:
                # 드라이버 종료 확실히 하기
                self.update_log("🔄 다운로드 완료, 리소스 정리 중...")
                self.current_download.stop()
                try:
                    self.current_download.join(timeout=2.0)
                except Exception as e:
                    self.update_log(f"⚠️ 다운로드 스레드 종료 중 오류 발생: {str(e)}")
                finally:
                    self.current_download = None
                    self.update_log("✅ 리소스 정리 완료")
            
            # 현재 다운로드 완료 처리
            for item in self.queue_tree.get_children():
                if self.queue_info[item]['status'] == 'downloading':
                    # 완료 상태로 업데이트
                    self.queue_info[item]['status'] = 'completed'
                    self.queue_tree.set(item, 'progress_text', "완료")
                    break
            
            # 다음 다운로드 처리
            waiting_items = [item for item in self.queue_tree.get_children() 
                           if self.queue_info[item]['status'] == 'waiting']
            
            if waiting_items:
                self.process_next_download()
            else:
                self.stop_button.config(state=tk.DISABLED)

    def stop_download(self):
        if self.current_download and self.current_download.is_alive():
            self.current_download.stop()
            self.update_log("⏹️ 다운로드를 중지합니다...")
            # 현재 항목 상태 업데이트
            for item in self.queue_tree.get_children():
                if self.queue_info[item]['status'] == 'downloading':
                    self.queue_info[item]['status'] = 'stopped'
                    self.queue_tree.set(item, 'progress_text', "중단됨")
                    break
            self.stop_button.config(state=tk.DISABLED)

    def save_state(self):
        """프로그램 상태를 파일에 저장"""
        try:
            state = {
                'queue_info': {},
                'log_content': self.log_text.get("1.0", tk.END),
                'file_list': [],
                'last_download_folder': self.folder_entry.get(),
                'save_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            # 대기열 정보 저장
            for item in self.queue_tree.get_children():
                values = self.queue_tree.item(item)['values']
                info = self.queue_info.get(item, {})
                state['queue_info'][item] = {
                    'album': values[0],
                    'progress_text': values[1],
                    'url': info.get('url', ''),
                    'folder': info.get('folder', ''),
                    'status': info.get('status', ''),
                    'total_files': info.get('total_files', 0)
                }

            # 파일 목록 저장
            for item in self.tree.get_children():
                values = self.tree.item(item)['values']
                state['file_list'].append({
                    'filename': values[0],
                    'status': values[1]
                })

            # 상태 파일 저장
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)

        except Exception as e:
            print(f"상태 저장 중 오류 발생: {str(e)}")

    def load_state(self):
        """저장된 프로그램 상태 불러오기"""
        try:
            if not os.path.exists(self.state_file):
                return

            with open(self.state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)

            # 로그 내용 복원
            self.log_text.delete("1.0", tk.END)
            self.log_text.insert("1.0", state.get('log_content', ''))

            # 마지막 다운로드 폴더 복원
            last_folder = state.get('last_download_folder', '')
            if last_folder and os.path.exists(last_folder):
                self.folder_entry.insert(0, last_folder)

            # 대기열 정보 복원
            for item_id, info in state.get('queue_info', {}).items():
                item = self.queue_tree.insert('', 'end', 
                    values=(info['album'], info['progress_text']))
                self.queue_info[item] = {
                    'url': info['url'],
                    'folder': info['folder'],
                    'status': info['status'],
                    'total_files': info['total_files']
                }

            # 파일 목록 복원
            for file_info in state.get('file_list', []):
                self.tree.insert('', 'end', 
                    values=(file_info['filename'], file_info['status']))

            # 저장 시간 표시
            save_time = state.get('save_time', '')
            if save_time:
                self.log_text.insert(tk.END, 
                    f"\n\n마지막 저장 시간: {save_time}\n" + "-" * 80 + "\n")

        except Exception as e:
            print(f"상태 불러오기 중 오류 발생: {str(e)}")

    def cleanup_resources(self):
        """프로그램 종료 전 리소스 정리"""
        try:
            print("\n=== 프로그램 종료 과정 시작 ===")
            print("1. 현재 상태 저장 시도")
            self.save_state()
            
            print("2. 현재 다운로드 상태 확인")
            if self.current_download and self.current_download.is_alive():
                print(f"   - 다운로드 스레드 상태: {self.current_download.is_alive()}")
                print(f"   - 드라이버 상태: {self.current_download.driver is not None}")
                print("   - 다운로드 중지 시도")
                self.current_download.stop()
                try:
                    print("   - 스레드 종료 대기")
                    self.current_download.join(timeout=2.0)
                except Exception as e:
                    print(f"   - 스레드 종료 중 오류: {str(e)}")
                finally:
                    print("   - 다운로드 객체 정리")
                    self.current_download = None
            
            print("3. 대기열 상태 확인")
            for item in self.queue_tree.get_children():
                status = self.queue_info[item]['status']
                print(f"   - 항목 상태: {status}")
                if status == 'downloading':
                    print("   - 다운로드 중인 항목 발견")
                    self.queue_info[item]['status'] = 'stopped'
            
            print("4. UI 리소스 정리")
            self.stop_button.config(state=tk.DISABLED)
            self.start_button.config(state=tk.DISABLED)
            
            print("5. 진행 상태바 초기화")
            self.current_progress["value"] = 0
            self.total_progress["value"] = 0
            
            print("=== 프로그램 종료 과정 완료 ===\n")
            
        except Exception as e:
            print(f"정리 중 오류 발생: {str(e)}")

    def on_closing(self):
        """프로그램 종료 처리"""
        print("\n=== 프로그램 종료 요청 감지 ===")
        print(f"1. 현재 종료 상태: {self.is_closing}")
        
        if self.is_closing:  # 이미 종료 중이면 무시
            print("2. 이미 종료 중이므로 무시")
            return
            
        # 다운로드 중인 항목이 있는지 확인
        has_active_downloads = any(info['status'] in ['downloading', 'waiting'] 
                                 for info in self.queue_info.values())
        
        print(f"2. 활성 다운로드 상태: {has_active_downloads}")
        
        if has_active_downloads:
            print("3. 사용자에게 종료 확인 요청")
            if not tk.messagebox.askokcancel("종료 확인", 
                "다운로드가 진행 중입니다.\n정말 종료하시겠습니까?"):
                print("4. 사용자가 종료를 취소함")
                return
        
        print("4. 종료 프로세스 시작")
        self.is_closing = True
        self.cleanup_resources()
        
        try:
            print("5. Tkinter 종료 시도")
            self.root.quit()
        except Exception as e:
            print(f"6. Tkinter 종료 중 오류: {str(e)}")
        finally:
            try:
                print("7. 창 파괴 시도")
                self.root.destroy()
            except Exception as e:
                print(f"8. 창 파괴 중 오류: {str(e)}")
            print("=== 프로그램 종료 완료 ===\n")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop() 