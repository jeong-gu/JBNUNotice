import os
import re
import sys
import sqlite3
import time
import hashlib
import ssl
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, parse_qs, urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib

# ----- 설정 로드 -----
load_dotenv()

# 공용
PAGES      = int(os.getenv("PAGES", "1"))
USER_AGENT = os.getenv("USER_AGENT", "JBNU-Notice-Mailer/1.1")
TIMEOUT    = int(os.getenv("TIMEOUT", "10"))
RETRY      = int(os.getenv("RETRY", "1"))
SEED_MODE  = os.getenv("SEED_MODE", "false").lower() == "true"

SMTP_HOST  = os.getenv("SMTP_HOST")
SMTP_PORT  = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER  = os.getenv("SMTP_USER")
SMTP_PASS  = os.getenv("SMTP_PASS")
MAIL_FROM  = os.getenv("MAIL_FROM", SMTP_USER)
MAIL_TO    = [x.strip() for x in os.getenv("MAIL_TO", SMTP_USER or "").split(",") if x.strip()]

# 학사공지(기존)
LIST_URL           = os.getenv("LIST_URL")
BASE_URL           = os.getenv("BASE_URL", "https://csai.jbnu.ac.kr")
ARTICLE_REGEX_STR  = os.getenv("ARTICLE_REGEX", r"^/bbs/csai/4929/(\d+)/artclView\.do(?:\?.*)?$")

# 교내공지(신규)
CAMPUS1_LIST_URLS          = os.getenv("CAMPUS1_LIST_URLS")  # 예: https://www.jbnu.ac.kr/web/news/notice/sub01.do?pageIndex=1&menu=2377
CAMPUS2_LIST_URLS          = os.getenv("CAMPUS2_LIST_URLS")
CAMPUS3_LIST_URLS          = os.getenv("CAMPUS3_LIST_URLS")
CAMPUS5_LIST_URLS          = os.getenv("CAMPUS5_LIST_URLS")

CAMPUS_BASE_URL           = os.getenv("CAMPUS_BASE_URL", "https://www.jbnu.ac.kr")
CAMPUS_ARTICLE_REGEX_STR  = os.getenv("CAMPUS_ARTICLE_REGEX", r".*\bmode=view\b.*\b(?:no|pid)=([0-9]+)\b")

# 소중대공지(신규)
SWUNIV_LIST_URL          = os.getenv("SWUNIV_LIST_URL")
SWUNIV_BASE_URL          = os.getenv("SWUNIV_BASE_URL", "https://swuniv.jbnu.ac.kr")
SWUNIV_ARTICLE_REGEX_STR = os.getenv("SWUNIV_ARTICLE_REGEX", r".*[\?&]program_id=([A-Za-z0-9]+)")

CONNECT_TIMEOUT = int(os.getenv("CONNECT_TIMEOUT", "3"))
READ_TIMEOUT    = int(os.getenv("READ_TIMEOUT", "10"))
REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)

DB_PATH    = "seen.sqlite"

# 소스 식별자(테이블에 저장)
SRC_BS     = "bs"      # 학사공지
SRC_CAMPUS = "campus"  # 교내공지
SRC_SWUNIV = "swuniv"

# TLS 호환 모드 on/off (.env 에 없으면 기본 켬)
JBNU_TLS_COMPAT = os.getenv("JBNU_TLS_COMPAT", "1").lower() in ("1", "true", "yes")

# KST
KST = timezone(timedelta(hours=9))

# ----- 유틸 -----
def log(msg):
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}")

# ---- TLS 어댑터(도메인 한정) ----
class TLSAdapter(HTTPAdapter):
    def __init__(self, ssl_context=None, **kwargs):
        self.ssl_context = ssl_context or ssl.create_default_context()
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self.ssl_context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs["ssl_context"] = self.ssl_context
        return super().proxy_manager_for(*args, **kwargs)

def make_legacy_ssl_context() -> ssl.SSLContext:
    """
    일부 구형 서버 호환:
      - TLS 1.2 강제 (TLS1.3 비활성)
      - 보안레벨 1 (구형 DH/RSA 허용)
    """
    ctx = ssl.create_default_context()
    if hasattr(ssl, "TLSVersion"):
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    try:
        # OpenSSL 3 보안레벨을 1로 낮춤
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    except Exception:
        pass
    return ctx

def get_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    # www.jbnu.ac.kr만 레거시 TLS 컨텍스트 사용
    if JBNU_TLS_COMPAT:
        legacy_ctx = make_legacy_ssl_context()
        s.mount("https://www.jbnu.ac.kr/", TLSAdapter(legacy_ctx))
    return s

# ----- DB -----
def init_db():
    """
    테이블 스키마(신형):
      seen(source TEXT, article_id TEXT, title TEXT, first_seen_ts INTEGER, PRIMARY KEY(source, article_id))
    구형(과거 버전):
      seen(article_id TEXT PRIMARY KEY, title TEXT, first_seen_ts INTEGER)
    구형이면 신형으로 마이그레이션
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 테이블 없으면 신형으로 생성
    cur.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            source TEXT,
            article_id TEXT,
            title TEXT,
            first_seen_ts INTEGER,
            PRIMARY KEY(source, article_id)
        )
    """)
    conn.commit()

    # 구형 스키마 여부 점검: source 컬럼 존재 확인
    cur.execute("PRAGMA table_info(seen)")
    cols = [row[1] for row in cur.fetchall()]
    if "source" not in cols:
        log("구형 DB 스키마 감지 → 신형으로 마이그레이션합니다.")
        cur.execute("ALTER TABLE seen RENAME TO seen_old")
        cur.execute("""
            CREATE TABLE seen (
                source TEXT,
                article_id TEXT,
                title TEXT,
                first_seen_ts INTEGER,
                PRIMARY KEY(source, article_id)
            )
        """)
        cur.execute("INSERT OR IGNORE INTO seen(source, article_id, title, first_seen_ts) "
                    "SELECT ?, article_id, title, first_seen_ts FROM seen_old", (SRC_BS,))
        cur.execute("DROP TABLE seen_old")
        conn.commit()
        log("마이그레이션 완료.")

    return conn

def is_seen(conn, source, article_id):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM seen WHERE source = ? AND article_id = ?", (source, article_id))
    return cur.fetchone() is not None

def mark_seen(conn, source, article_id, title):
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO seen(source, article_id, title, first_seen_ts) VALUES (?, ?, ?, ?)",
                (source, article_id, title, int(time.time())))
    conn.commit()

from urllib.parse import urlparse, parse_qs, urlencode

def fetch_list_pages(list_url: str):
    """
    목록 페이지를 1~PAGES까지 요청.
    - 쿼리에 이미 page 파라미터가 있으면 그 이름을 그대로 사용
    - 없으면 www.jbnu.ac.kr 은 pageIndex, 그 외(학과/소중대 등)는 page 사용
    - 시작 페이지는 쿼리에 있으면 거기서부터, 없으면 1부터
    """
    if not list_url:
        return []

    session = get_session()
    html_list = []

    parsed = urlparse(list_url)
    qs = parse_qs(parsed.query)

    # 1) 쿼리에 이미 있는 키를 우선 사용
    cand_keys = ["pageIndex", "page", "pageNo", "curPage", "pageNum"]
    page_param = next((k for k in cand_keys if k in qs), None)

    # 2) 없으면 호스트에 따라 결정: www.jbnu.ac.kr -> pageIndex, 나머지는 page
    host = (parsed.hostname or "").lower()
    if page_param is None:
        page_param = "pageIndex" if host == "www.jbnu.ac.kr" else "page"

    # 시작 페이지
    try:
        start_page = int(qs.get(page_param, ["1"])[0])
    except ValueError:
        start_page = 1

    last_exc = None
    for i in range(PAGES):
        pno = start_page + i
        new_qs = {k: v[:] for k, v in qs.items()}
        new_qs[page_param] = [str(pno)]
        new_query = urlencode(new_qs, doseq=True)
        url = parsed._replace(query=new_query).geturl()

        for _ in range(RETRY + 1):
            try:
                resp = session.get(url, timeout=TIMEOUT)  # 기존 코드 스타일 유지
                resp.raise_for_status()
                html_list.append(resp.text)
                log(f"[fetch] {host} {page_param}={pno} -> {url}")
                break
            except Exception as e:
                last_exc = e
                time.sleep(2)
        else:
            raise last_exc

    return html_list



# --- JBNU 전용: onclick에서 상세 URL 만들기 ---
_pf_detailmove_re = re.compile(r"pf_DetailMove\(['\"](\d+)['\"]\)", re.I)

def build_jbnu_detail_href(id_str: str, base_url: str, list_url: str | None) -> str:
    """
    예시: onclick="pf_DetailMove('191066')"  -> /web/Board/191067/detailView.do?menu=2377&pageIndex=1
    * 일부 게시판은 +1이 아닐 수 있음. 그 경우 board_id = id_str 로 바꿔 쓰세요.
    """
    try:
        board_id = str(int(id_str))  # 필요시 +1 제거
    except ValueError:
        board_id = id_str

    params = {}
    if list_url:
        u = urlparse(list_url)
        qs = parse_qs(u.query)
        if "menu" in qs and qs["menu"]:
            params["menu"] = qs["menu"][0]
        if "pageIndex" in qs and qs["pageIndex"]:
            params["pageIndex"] = qs["pageIndex"][0]

    q = f"?{urlencode(params)}" if params else ""
    detail_path = f"/web/Board/{board_id}/detailView.do{q}"
    return urljoin(base_url, detail_path)

def parse_items(html: str, article_href_re: re.Pattern, base_url: str, list_url: str | None = None):
    """
    목록 HTML에서 (article_id, title, href, date_text)를 추출합니다.
    - a[href="javascript:;"] + onclick="pf_DetailMove('NNN')" 패턴 지원
    - 일반 href에 대한 정규식 매칭도 지원(search)
    """
    soup = BeautifulSoup(html, "lxml")
    items = []

    for a in soup.find_all("a", href=True):
        raw_href = a["href"].strip()
        title = a.get_text(strip=True) or "(제목 없음)"

        article_id = None
        href = None

        # 1) javascript + pf_DetailMove('NNN') 처리
        if raw_href.lower().startswith("javascript"):
            onclick = a.get("onclick", "") or ""
            m_js = _pf_detailmove_re.search(onclick)
            if m_js:
                onclick_id = m_js.group(1)
                href = build_jbnu_detail_href(onclick_id, base_url, list_url)
                try:
                    article_id = str(int(onclick_id) + 1)
                except ValueError:
                    article_id = onclick_id

        # 2) 일반 href 정규식 (match -> search)
        if article_id is None:
            m = article_href_re.search(raw_href)
            if m:
                article_id = m.group(1)
                href = urljoin(base_url, raw_href)

        if article_id is None or href is None:
            continue

        # 같은 행(tr)에서 날짜 추정
        date_text = ""
        tr = a.find_parent("tr")
        if tr:
            tds = tr.find_all("td")
            candidates = [td.get_text(strip=True) for td in tds if td.get_text(strip=True)]
            for c in candidates[::-1]:
                if re.search(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", c):
                    date_text = c
                    break

        items.append({
            "article_id": article_id,
            "title": title,
            "href": href,
            "date": date_text
        })

    # ID 기준 유니크
    unique = {}
    for it in items:
        unique[it["article_id"]] = it
    return list(unique.values())

# ----- 메일 -----
def send_email(new_items, subject_prefix: str, mail_to_list):
    if not mail_to_list:
        log("MAIL_TO가 비어 있어 메일을 보내지 않습니다.")
        return

    subject_dt = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    subject = f"{subject_prefix} {subject_dt} 기준 신규 {len(new_items)}건"

    rows = []
    for it in new_items:
        rows.append(f"""
        <tr>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;">
                <div style="font-weight:600; font-size:14px; margin-bottom:4px;">
                    <a href="{it['href']}" target="_blank" style="color:#1a73e8;text-decoration:none;">{escape_html(it['title'])}</a>
                </div>
                <div style="font-size:12px;color:#555;">{escape_html(it['date'])}</div>
            </td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee; text-align:right;">
                <a href="{it['href']}" target="_blank" style="display:inline-block;padding:6px 10px;border:1px solid #1a73e8;border-radius:6px;text-decoration:none;">바로가기</a>
            </td>
        </tr>
        """)

    html = f"""
    <div style="font-family:system-ui,Segoe UI,Apple SD Gothic Neo,sans-serif;">
      <h2 style="margin:0 0 12px 0;">{subject_prefix} 신규 {len(new_items)}건</h2>
      <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
        {''.join(rows)}
      </table>
      <div style="color:#999;font-size:11px;margin-top:12px;">
        본 메일은 자동 발송되었습니다. (Asia/Seoul)
      </div>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = MAIL_FROM
    msg["To"] = ", ".join(mail_to_list)

    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        server.ehlo()
        if SMTP_PORT == 587:
            server.starttls()
            server.ehlo()
        if SMTP_USER and SMTP_PASS:
            server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(MAIL_FROM, mail_to_list, msg.as_string())

def escape_html(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ----- 파이프라인 공통 -----
def run_pipeline(conn, source_key: str, list_url: str, base_url: str, regex_str: str,
                 subject_prefix: str, mail_to_list):
    if not list_url:
        log(f"[{source_key}] LIST_URL이 비어 있어 건너뜁니다.")
        return

    log(f"[{source_key}] 목록 수집 시작: {list_url}")
    html_list = fetch_list_pages(list_url)

    article_href_re = re.compile(regex_str)
    all_items = []
    for html in html_list:
        all_items.extend(parse_items(html, article_href_re, base_url, list_url))

    log(f"[{source_key}] 파싱된 항목: {len(all_items)}건")

    if SEED_MODE:
        for it in all_items:
            mark_seen(conn, source_key, it["article_id"], it["title"])
        log(f"[{source_key}] SEED_MODE: 현 시점 {len(all_items)}건을 '이미 본 것'으로 기록. 메일 발송 없음.")
        return

    new_items = [it for it in all_items if not is_seen(conn, source_key, it["article_id"])]

    if new_items:
        new_items_sorted = sorted(new_items, key=lambda x: int(re.sub(r"\D", "", x["article_id"]) or "0"), reverse=True)
        send_email(new_items_sorted, subject_prefix, mail_to_list)
        for it in new_items_sorted:
            mark_seen(conn, source_key, it["article_id"], it["title"])
        log(f"[{source_key}] 신규 {len(new_items_sorted)}건 메일 발송 및 기록 완료.")
    else:
        log(f"[{source_key}] 신규 공지 없음.")

# ----- 메인 -----
def main():
    if not LIST_URL and not CAMPUS1_LIST_URLS and not CAMPUS2_LIST_URLS and not CAMPUS3_LIST_URLS and not CAMPUS5_LIST_URLS and not SWUNIV_LIST_URL:
        log("학사공지 LIST_URL, 교내공지 CAMPUS_LIST_URLS, 소중대 SWUNIV_LIST_URL 모두 비었습니다. .env를 확인하세요.")
        sys.exit(1)

    conn = init_db()

    # 1) 학사공지
    if LIST_URL:
        run_pipeline(
            conn=conn,
            source_key=SRC_BS,
            list_url=LIST_URL,
            base_url=BASE_URL,
            regex_str=ARTICLE_REGEX_STR,
            subject_prefix="[학사공지 알림]",
            mail_to_list=MAIL_TO,
        )

    # 2) 교내공지 (주어진 URL 하나만 써도 OK)
    if CAMPUS1_LIST_URLS:
        run_pipeline(
            conn=conn,
            source_key=SRC_CAMPUS,
            list_url=CAMPUS1_LIST_URLS,
            base_url=CAMPUS_BASE_URL,
            regex_str=CAMPUS_ARTICLE_REGEX_STR,
            subject_prefix="[교내공지 알림]",
            mail_to_list=MAIL_TO,
        )
        
    if CAMPUS2_LIST_URLS:
        run_pipeline(
            conn=conn,
            source_key=SRC_CAMPUS,
            list_url=CAMPUS2_LIST_URLS,
            base_url=CAMPUS_BASE_URL,
            regex_str=CAMPUS_ARTICLE_REGEX_STR,
            subject_prefix="[학생공지 알림]",
            mail_to_list=MAIL_TO,
        )
    
    if CAMPUS3_LIST_URLS:
        run_pipeline(
            conn=conn,
            source_key=SRC_CAMPUS,
            list_url=CAMPUS3_LIST_URLS,
            base_url=CAMPUS_BASE_URL,
            regex_str=CAMPUS_ARTICLE_REGEX_STR,
            subject_prefix="[교내채용 알림]",
            mail_to_list=MAIL_TO,
        )
        
    if CAMPUS5_LIST_URLS:
        run_pipeline(
            conn=conn,
            source_key=SRC_CAMPUS,
            list_url=CAMPUS5_LIST_URLS,
            base_url=CAMPUS_BASE_URL,
            regex_str=CAMPUS_ARTICLE_REGEX_STR,
            subject_prefix="[특강&세미나 알림]",
            mail_to_list=MAIL_TO,
        )

    # 3) 소중대공지
    if SWUNIV_LIST_URL:
        run_pipeline(
            conn=conn,
            source_key=SRC_SWUNIV,
            list_url=SWUNIV_LIST_URL,
            base_url=SWUNIV_BASE_URL,
            regex_str=SWUNIV_ARTICLE_REGEX_STR,
            subject_prefix="[소중대공지 알림]",
            mail_to_list=MAIL_TO,
        )

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"오류 발생: {e}")
        raise
