"""가족 캘린더 | 실행: streamlit run app.py
배포: 함께 제공한 zofflsej.txt를 requirements.txt로 바꾸어 GitHub에 업로드.
Streamlit Cloud Secrets 예시 (선택):
APP_URL = "https://your-app.streamlit.app"
FAMILY_PASSWORD = "가족만 아는 비밀번호"
GITHUB_TOKEN = "데이터 저장소에만 Contents: Read and write 권한을 가진 토큰"
GITHUB_REPO = "owner/private-calendar-data"
GITHUB_BRANCH = "main"
데이터 저장소는 README 등으로 초기화된 비공개 저장소를 권장합니다.
토큰/비밀번호를 이 파일이나 GitHub 코드에 직접 작성하지 마세요.
GitHub 저장 설정이 없으면 로컬 SQLite 사용 (Cloud 재시작 시 소실 가능).
음성은 브라우저 버튼을 누를 때 재생되며, 앱을 닫은 동안 알림은 지원하지 않습니다.
"""
import base64
import calendar
import hmac
import html
import io
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import qrcode
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="우리 가족 캘린더", page_icon="🏡", layout="wide")


def secret(key, default=""):
    try:
        return str(st.secrets.get(key, default))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return default


def today():
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def github_enabled():
    return bool(secret("GITHUB_TOKEN") and secret("GITHUB_REPO"))


def api(method="GET", payload=None):
    repo = secret("GITHUB_REPO")
    if len(repo.split("/")) != 2:
        raise ValueError("GITHUB_REPO는 owner/repo 형식이어야 합니다.")
    owner, name = (urllib.parse.quote(p, safe="") for p in repo.split("/"))
    url = f"https://api.github.com/repos/{owner}/{name}/contents/family_calendar.json"
    if method == "GET":
        url += "?ref=" + urllib.parse.quote(secret("GITHUB_BRANCH", "main"), safe="")
    req = urllib.request.Request(url, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": "Bearer " + secret("GITHUB_TOKEN"),
                 "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                 "User-Agent": "FamilyCalendar", "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def remote_read():
    try:
        raw = api()
        events = json.loads(base64.b64decode(raw["content"]).decode("utf-8"))
        if not isinstance(events, list):
            raise ValueError("저장 데이터 형식이 올바르지 않습니다.")
        return events, raw["sha"]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # 저장소/브랜치 접근 오류도 404일 수 있으며 저장 시 사용자에게 표시됩니다.
            return [], None
        raise


def db():
    conn = sqlite3.connect("family_calendar.db", timeout=20)
    conn.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    return conn


def read_events():
    if github_enabled():
        return remote_read()[0]
    with db() as conn:
        return [json.loads(row[0]) for row in conn.execute("SELECT data FROM events")]


def mutate(event=None, delete_id=None):
    if github_enabled():
        # SHA 충돌 시 최신 목록을 다시 읽고 변경한 일정 하나만 병합합니다.
        for attempt in range(3):
            events, sha = remote_read()
            key = delete_id or event["id"]
            events = [e for e in events if e["id"] != key]
            if event is not None:
                events.append(event)
            payload = {"message": "Update family calendar", "branch": secret("GITHUB_BRANCH", "main"),
                       "content": base64.b64encode(json.dumps(events, ensure_ascii=False).encode()).decode()}
            if sha:
                payload["sha"] = sha
            try:
                api("PUT", payload)
                return
            except urllib.error.HTTPError as exc:
                if exc.code not in (409, 422) or attempt == 2:
                    raise
    else:
        with db() as conn:
            if delete_id:
                conn.execute("DELETE FROM events WHERE id=?", (delete_id,))
            else:
                conn.execute("INSERT OR REPLACE INTO events VALUES (?, ?)",
                             (event["id"], json.dumps(event, ensure_ascii=False)))


def occurs(event, day):
    start = date.fromisoformat(event["date"])
    if event.get("annual"):
        # 2월 29일 반복 일정은 평년에 2월 28일로 표시합니다.
        actual_day = min(start.day, calendar.monthrange(day.year, start.month)[1])
        return day >= start and (day.month, day.day) == (start.month, actual_day)
    return start == day


def on_day(events, day):
    return sorted((e for e in events if occurs(e, day)), key=lambda e: (e.get("time", ""), e["title"]))


def announcement(events, day):
    items = on_day(events, day)
    if not items:
        return "오늘은 일정이 없습니다."
    names = [f'{e["member"]} {e["title"]}'.strip() for e in items]
    return "오늘은 " + ", ".join(names) + "이 있습니다."


def speak(text):
    # JSON을 script에 안전하게 넣어 사용자 입력으로 HTML/JS가 실행되지 않게 합니다.
    encoded = json.dumps(text, ensure_ascii=True).replace("<", "\\u003c")
    components.html('''<button id="speak" style="padding:12px 18px;border:0;border-radius:12px;
background:#41685a;color:white;font-size:16px;cursor:pointer">🔊 오늘 일정 듣기</button>
<span id="status" style="font:14px sans-serif"></span><script>
const text=TEXT;
document.getElementById('speak').onclick=()=>{
if(!('speechSynthesis' in window)){document.getElementById('status').textContent='음성을 지원하지 않는 브라우저입니다.';return;}
window.speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(text);u.lang='ko-KR';u.rate=0.95;
u.onerror=()=>document.getElementById('status').textContent='음성 재생이 차단되었습니다. 브라우저 설정을 확인하세요.';
window.speechSynthesis.speak(u);};</script>'''.replace("TEXT", encoded), height=70)


def calendar_html(year, month, events):
    esc = html.escape
    parts = ['<div class="month"><h3>' + str(month) + '월</h3><table class="cal"><thead><tr>']
    parts += [f'<th>{name}</th>' for name in ['월', '화', '수', '목', '금', '토', '일']]
    parts.append('</tr></thead><tbody>')
    for week in calendar.monthcalendar(year, month):
        parts.append('<tr>')
        for n in week:
            if not n:
                parts.append('<td class="blank"></td>')
                continue
            day = date(year, month, n)
            cls = ' class="today"' if day == today() else ''
            parts.append(f'<td{cls}><b>{n}</b>')
            for e in on_day(events, day):
                label = ('🎂 ' if e['kind'] == '생일' else '📌 ') + e['member'] + ' ' + e['title']
                parts.append('<span class="event">' + esc(label) + '</span>')
            parts.append('</td>')
        parts.append('</tr>')
    parts.append('</tbody></table></div>')
    return ''.join(parts)


def event_editor(events):
    st.subheader("날짜를 선택해 추가·수정하기")
    selected_day = st.date_input("일정 날짜", value=st.session_state.get("chosen_day", today()),
                                 min_value=date(1900, 1, 1), max_value=date(2200, 12, 31), key="edit_day")
    choices = on_day(events, selected_day)
    ids = [None] + [e['id'] for e in choices]
    by_id = {e['id']: e for e in choices}
    selected_id = st.selectbox("새 일정 또는 수정할 일정", ids,
        format_func=lambda x: "＋ 새 일정" if x is None else f"{by_id[x]['member']} · {by_id[x]['title']}")
    current = by_id.get(selected_id)
    suffix = str(selected_id or selected_day)
    with st.form("editor_" + suffix):
        title = st.text_input("일정 이름 *", value=current['title'] if current else '', max_chars=100)
        member = st.text_input("가족 이름", value=current['member'] if current else '', max_chars=40)
        kind = st.selectbox("종류", ['일정', '생일'], index=1 if current and current['kind'] == '생일' else 0)
        when = st.date_input("기준 날짜 / 변경할 날짜", value=date.fromisoformat(current['date']) if current else selected_day,
                             min_value=date(1900, 1, 1), max_value=date(2200, 12, 31))
        time = st.text_input("시간 (선택, 예: 14:30)", value=current.get('time', '') if current else '', max_chars=5)
        annual = st.checkbox("매년 반복 (생일은 자동 적용)", value=current.get('annual', False) if current else False)
        note = st.text_area("메모", value=current.get('note', '') if current else '', max_chars=1000)
        saved = st.form_submit_button("💾 일정 저장", type="primary")
    if saved:
        if not title.strip():
            st.error("일정 이름을 입력해 주세요.")
        elif time and (len(time) != 5 or time[2] != ':' or not time.replace(':', '').isdigit()
                       or int(time[:2]) > 23 or int(time[3:]) > 59):
            st.error("시간은 00:00~23:59 형식으로 입력해 주세요.")
        else:
            try:
                mutate(event={'id': selected_id or str(uuid.uuid4()), 'title': title.strip(),
                              'member': member.strip(), 'kind': kind, 'date': when.isoformat(),
                              'time': time, 'annual': annual or kind == '생일', 'note': note})
                st.session_state['flash'] = "일정을 저장했습니다."
                st.rerun()
            except Exception:
                st.error("저장하지 못했습니다. 네트워크 또는 GitHub 저장소·브랜치·토큰 쓰기 권한을 확인해 주세요.")
    if current:
        confirm = st.checkbox("이 일정 전체를 삭제합니다 (매년 반복도 모두 삭제)", key="del_" + suffix)
        if st.button("일정 삭제", disabled=not confirm, key="delete_" + suffix):
            try:
                mutate(delete_id=selected_id)
                st.session_state['flash'] = "일정을 삭제했습니다."
                st.rerun()
            except Exception:
                st.error("삭제하지 못했습니다. 저장 연결을 확인해 주세요.")
    st.caption("생일은 양력 기준입니다. 2월 29일 생일은 평년에 2월 28일로 안내합니다. 반복 일정 수정은 모든 연도에 반영됩니다.")


def main():
    password = secret("FAMILY_PASSWORD")
    if password and not st.session_state.get("authorized"):
        st.title("🏡 우리 가족 캘린더")
        with st.form("login"):
            given = st.text_input("가족 비밀번호", type="password")
            submitted = st.form_submit_button("들어가기")
        if submitted:
            if hmac.compare_digest(given.encode(), password.encode()):
                st.session_state['authorized'] = True
                st.rerun()
            else:
                st.error("비밀번호가 다릅니다.")
        st.stop()
    st.markdown('''<style>
.stApp{background:#f8faf7}h1,h2,h3{color:#29483c}
.month{background:white;border:1px solid #dce6dc;border-radius:16px;padding:14px;margin-bottom:18px;overflow-x:auto}
.cal{width:100%;border-collapse:collapse;table-layout:fixed;min-width:300px}
.cal th{padding:8px 0;color:#53665a;font-size:13px}.cal td{border:1px solid #e4eae3;vertical-align:top;height:80px;padding:5px;font-size:13px;overflow-wrap:anywhere}
.cal th:last-child{color:#b34242}.cal td.today{background:#e5f2d8;outline:2px solid #4e7342;outline-offset:-2px}
.event{display:block;background:#edf2ee;border-radius:5px;padding:3px;margin-top:4px;font-size:11px;color:#29483c}.blank{background:#f5f6f4}
</style>''', unsafe_allow_html=True)
    st.title("🏡 우리 가족 캘린더")
    st.caption("함께 기억하는 생일과 소중한 약속 · 한국 시간 기준")
    pages = ['🏠 오늘', '🗓️ 월간', '📅 연간 12달', '✏️ 일정 관리', '📱 QR 공유']
    if 'page' not in st.session_state:
        st.session_state['page'] = pages[0]
    st.sidebar.title("우리 가족의 시간")
    page = st.sidebar.radio("화면 이동", pages, key="page")
    index = pages.index(page)
    def navigate(step):
        st.session_state['page'] = pages[(index + step) % len(pages)]
    a, b = st.columns(2)
    a.button("← 이전 화면", on_click=navigate, args=(-1,), use_container_width=True)
    b.button("다음 화면 →", on_click=navigate, args=(1,), use_container_width=True)
    if github_enabled():
        st.sidebar.success("GitHub 공유 저장 연결 설정됨")
        if not password:
            st.warning("공개 접속자는 일정을 보고 수정할 수 있습니다. Secrets에 FAMILY_PASSWORD를 설정하세요.")
    else:
        st.sidebar.warning("임시 로컬 저장 모드: Cloud 재시작 시 소실될 수 있습니다.")
    st.sidebar.button("🔄 최신 일정 새로고침", on_click=lambda: None)
    if password and st.sidebar.button("로그아웃"):
        st.session_state['authorized'] = False
        st.rerun()
    try:
        events = read_events()
    except Exception:
        st.error("일정을 읽지 못했습니다. GitHub 저장 설정과 네트워크를 확인해 주세요.")
        st.stop()
    if st.session_state.get('flash'):
        st.success(st.session_state.pop('flash'))
    now = today()
    if page == pages[0]:
        st.subheader(now.strftime("%Y년 %m월 %d일"))
        text = announcement(events, now)
        st.success(text) if on_day(events, now) else st.info(text)
        speak(text)
        st.caption("앱을 열고 ‘오늘 일정 듣기’를 누르면 음성으로 안내합니다. 앱을 닫았을 때 자동 알림은 전송되지 않습니다.")
        st.subheader("다가오는 7일")
        found = False
        for delta in range(7):
            day = now + timedelta(days=delta)
            for e in on_day(events, day):
                found = True
                with st.container(border=True):
                    st.write(f"{day:%m/%d} · {e.get('time') or '종일'} · {e['member']} {e['title']}")
                    if e.get('note'):
                        st.text(e['note'])
        if not found:
            st.write("다가오는 7일 동안 등록된 일정이 없습니다.")
    elif page in (pages[1], pages[2]):
        year = int(st.number_input("연도", min_value=1900, max_value=2200, value=now.year, step=1))
        if page == pages[1]:
            month = st.selectbox("월", range(1, 13), index=now.month - 1)
            st.markdown(calendar_html(year, month, events), unsafe_allow_html=True)
            st.caption("아래에서 날짜를 선택해 해당 날짜의 일정을 추가·수정하세요.")
            event_editor(events)
        else:
            st.subheader(f"{year}년, 열두 달의 기록")
            for month in range(1, 13):
                st.markdown(calendar_html(year, month, events), unsafe_allow_html=True)
            st.caption("날짜별 편집은 ‘일정 관리’ 또는 ‘월간’ 화면을 이용하세요.")
    elif page == pages[3]:
        event_editor(events)
        st.divider()
        st.subheader("등록 일정 백업")
        st.download_button("전체 일정 JSON 다운로드", json.dumps(events, ensure_ascii=False, indent=2),
                           file_name=f"family_calendar_{now}.json", mime="application/json")
        st.caption(f"등록된 원본 일정: {len(events)}개")
    else:
        st.subheader("가족에게 QR로 초대하기")
        st.write("배포 완료 후 실제 앱 주소를 입력하세요. QR을 휴대폰 카메라로 읽으면 앱이 열립니다.")
        url = st.text_input("배포된 앱 HTTPS 주소", value=secret("APP_URL"), placeholder="https://your-app.streamlit.app")
        if url:
            parsed = urllib.parse.urlparse(url.strip())
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                st.error("로그인 정보가 포함되지 않은 올바른 HTTPS 주소를 입력해 주세요.")
            else:
                img = qrcode.make(url.strip())
                data = io.BytesIO()
                img.save(data, format='PNG')
                st.image(data.getvalue(), width=260)
                st.download_button("QR 이미지 다운로드", data.getvalue(), "family_calendar_qr.png", "image/png")
                st.link_button("앱 주소 열기", url.strip())
        st.info("QR은 앱 접속 링크입니다. 비밀번호는 QR에 포함되지 않습니다. 홈 화면에 추가하면 앱처럼 편하게 사용할 수 있습니다.")
        with st.expander("GitHub + Streamlit 배포 및 공유 저장 설정"):
            st.markdown('''1. GitHub에 `app.py`와 의존성 파일을 올립니다. `zofflsej.txt`는 **requirements.txt**로 이름을 바꿔 주세요.
2. Streamlit Community Cloud에서 저장소를 연결하고 실행 파일을 `app.py`로 지정합니다.
3. 가족끼리 영구 공유하려면 별도의 **비공개 데이터 저장소**를 만들고 README로 초기화합니다.
4. 그 데이터 저장소에만 Contents 읽기/쓰기 권한이 있는 토큰을 만들어 앱 Settings → Secrets에 아래 항목을 입력합니다.
5. 배포된 주소를 APP_URL에 설정하면 QR 주소에 자동으로 채워집니다.

```toml
APP_URL = "https://your-app.streamlit.app"
FAMILY_PASSWORD = "가족만 아는 비밀번호"
GITHUB_REPO = "owner/private-calendar-data"
GITHUB_BRANCH = "main"
GITHUB_TOKEN = "발급받은 토큰"
```
토큰과 비밀번호는 GitHub 코드에 올리지 마세요. 일정은 데이터 저장소의 `family_calendar.json`에 자동 저장됩니다. 토큰이 만료되면 갱신해야 합니다. GitHub 저장은 소규모 가족용이며 같은 일정을 동시에 수정하면 마지막 저장이 반영됩니다. 비밀번호는 간편 공유 잠금이며 사용자별 권한이나 로그인 시도 제한은 없습니다.''')


if __name__ == '__main__':
    main()
