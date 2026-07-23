from __future__ import annotations

import argparse
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parent.parent
FONT_PATH = Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf")
GREEN = colors.HexColor("#315B3A")
ORANGE = colors.HexColor("#D86540")
INK = colors.HexColor("#172019")
MUTED = colors.HexColor("#657067")
PALE = colors.HexColor("#EEF5EE")
LINE = colors.HexColor("#DDE5DC")


def register_fonts() -> str:
    if FONT_PATH.exists():
        pdfmetrics.registerFont(TTFont("Korean", str(FONT_PATH)))
        return "Korean"
    return "Helvetica"


def styles(font: str):
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("TitleK", parent=base["Title"], fontName=font, fontSize=28, leading=37, textColor=INK, spaceAfter=12 * mm),
        "subtitle": ParagraphStyle("SubtitleK", parent=base["Normal"], fontName=font, fontSize=12, leading=20, textColor=MUTED),
        "h1": ParagraphStyle("H1K", parent=base["Heading1"], fontName=font, fontSize=20, leading=27, textColor=GREEN, spaceBefore=4 * mm, spaceAfter=5 * mm),
        "h2": ParagraphStyle("H2K", parent=base["Heading2"], fontName=font, fontSize=13, leading=19, textColor=INK, spaceBefore=4 * mm, spaceAfter=2 * mm),
        "body": ParagraphStyle("BodyK", parent=base["BodyText"], fontName=font, fontSize=9.3, leading=15, textColor=INK, spaceAfter=2.5 * mm, wordWrap="CJK"),
        "small": ParagraphStyle("SmallK", parent=base["BodyText"], fontName=font, fontSize=7.7, leading=12, textColor=MUTED, wordWrap="CJK"),
        "callout": ParagraphStyle("CalloutK", parent=base["BodyText"], fontName=font, fontSize=10, leading=16, textColor=GREEN, leftIndent=5 * mm, rightIndent=5 * mm, spaceAfter=4 * mm, wordWrap="CJK"),
        "center": ParagraphStyle("CenterK", parent=base["BodyText"], fontName=font, fontSize=9, leading=14, alignment=TA_CENTER, textColor=INK, wordWrap="CJK"),
        "table": ParagraphStyle("TableK", parent=base["BodyText"], fontName=font, fontSize=7.5, leading=11, textColor=INK, wordWrap="CJK"),
        "table_head": ParagraphStyle("TableHeadK", parent=base["BodyText"], fontName=font, fontSize=7.8, leading=11, textColor=colors.white, alignment=TA_LEFT, wordWrap="CJK"),
    }


def p(text: str, style) -> Paragraph:
    return Paragraph(text, style)


def bullet(text: str, style) -> Paragraph:
    return Paragraph(f"• {text}", style)


def table(data, widths, st, *, header=True):
    converted = []
    for row_index, row in enumerate(data):
        converted.append([p(str(cell), st["table_head"] if header and row_index == 0 else st["table"]) for cell in row])
    result = Table(converted, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    rules = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), .4, LINE),
    ]
    if header:
        rules.extend([("BACKGROUND", (0, 0), (-1, 0), GREEN), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)])
        if len(data) > 1:
            rules.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9F6")]))
    result.setStyle(TableStyle(rules))
    return result


def page_decorator(student: str, font: str):
    def decorate(canvas, document):
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(LINE)
        canvas.line(20 * mm, 16 * mm, width - 20 * mm, 16 * mm)
        canvas.setFont(font, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 10.5 * mm, "Tweety | Secure Coding Development Report")
        canvas.drawRightString(width - 20 * mm, 10.5 * mm, f"{student}  ·  {document.page}")
        canvas.restoreState()
    return decorate


def build(args) -> None:
    font = register_fonts()
    st = styles(font)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output), pagesize=A4, rightMargin=20 * mm, leftMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=22 * mm, title="Tweety 개발 보고서", author=args.student,
    )
    story = []

    story.extend([
        Spacer(1, 25 * mm),
        p("SECURE CODING PROJECT", st["subtitle"]),
        p("Tiny Second-hand<br/>Shopping Platform", st["title"]),
        Table([["", ""]], colWidths=[35 * mm, 115 * mm], rowHeights=[3 * mm], style=TableStyle([("BACKGROUND", (0, 0), (0, 0), ORANGE), ("BACKGROUND", (1, 0), (1, 0), PALE)])),
        Spacer(1, 14 * mm),
        p("요구사항 분석부터 설계·구현·테스트·유지보수까지,<br/>보안 약점을 선제적으로 제거한 개발 전 과정 보고서", st["subtitle"]),
        Spacer(1, 30 * mm),
        table([
            ["작성자", args.student], ["분반", f"{args.class_number}반"], ["작성일", "2026-07-21"],
            ["Public GitHub", args.repo_url], ["검증 결과", "자동 테스트 21/21 성공 · 실제 브라우저 검수 완료"],
        ], [34 * mm, 125 * mm], st, header=False),
        PageBreak(),
    ])

    story.extend([
        p("목차", st["h1"]),
        table([
            ["01", "프로젝트 개요와 결과", "3"], ["02", "요구사항 분석", "4"], ["03", "시스템 설계", "5"],
            ["04", "구현", "7"], ["05", "보안 약점과 개선", "8"], ["06", "체크리스트와 테스트", "10"],
            ["07", "유지보수 및 AI 활용", "12"],
        ], [18 * mm, 120 * mm, 18 * mm], st, header=False),
        Spacer(1, 10 * mm),
        p("핵심 성과", st["h2"]),
        p("수업 자료의 최소 요구사항 7개를 모두 구현했다. 기능별 정상 동작뿐 아니라 공격·오용 시나리오를 자동 테스트로 재현했으며, 실제 브라우저와 390px 모바일 화면까지 점검했다.", st["callout"]),
        table([
            ["요구사항", "구현 결과", "보안 통제"],
            ["가입", "가입·로그인·고유 닉네임·프로필", "PBKDF2, UNIQUE, 세션 회전"],
            ["상품", "사진 등록·조회·검색·수정·삭제", "이미지 검증, XSS/SQLi/IDOR 방어"],
            ["소통", "상품별 판매자 1:1 메시지", "상품·참가자 권한, 길이 제한"],
            ["차단", "사용자 차단·신고·관리자 조치", "관계 기반 접근 차단, 신고 임계값"],
            ["송금", "구매자 포인트 → 판매자 포인트", "원자적 트랜잭션, 조건부 UPDATE"],
            ["검색", "상품명·설명 검색", "바인딩 쿼리, LIKE 이스케이프"],
            ["관리", "신고·차단·계정 정지 대시보드", "서버 측 관리자 역할 재검사"],
        ], [30 * mm, 62 * mm, 65 * mm], st),
        PageBreak(),
    ])

    story.extend([
        p("01. 프로젝트 개요와 결과", st["h1"]),
        p("Tweety는 중고 상품을 등록·검색·구매하고 사용자끼리 소통할 수 있는 교육용 웹 플랫폼이다. Python 3.11+, SQLite와 사진 전체 검증·재인코딩을 위한 Pillow를 사용했다. 보안은 완성 후 점검 항목이 아니라 요구사항과 데이터 모델의 제약으로 설계했다.", st["body"]),
        p("최종 사용자 화면", st["h2"]),
    ])
    home = ROOT / "work/home.png"
    product = ROOT / "work/product.png"
    images = []
    if home.exists():
        images.append(Image(str(home), width=78 * mm, height=65 * mm))
    if product.exists():
        images.append(Image(str(product), width=78 * mm, height=72 * mm))
    if len(images) == 2:
        img_table = Table([images], colWidths=[80 * mm, 80 * mm])
        img_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), .5, LINE), ("INNERGRID", (0, 0), (-1, -1), .5, LINE), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        story.append(img_table)
    story.extend([
        Spacer(1, 5 * mm),
        p("최종 결과", st["h2"]),
        bullet("자동 보안·기능 테스트 27개 전부 성공", st["body"]),
        bullet("회원가입 → 인증 세션 회전 → 상품 등록 → 상세 조회 실제 브라우저 성공", st["body"]),
        bullet("데스크톱과 390px 모바일 레이아웃 검수 및 가로 넘침 수정", st["body"]),
        bullet("README 실행법, 관리자 생성법, 운영 보안 권고, 체크리스트 제공", st["body"]),
        PageBreak(),
    ])

    story.extend([
        p("02. 요구사항 분석", st["h1"]),
        p("기능 요구사항", st["h2"]),
        table([
            ["ID", "요구사항", "수용 기준"],
            ["FR-01", "계정", "중복 없는 아이디·닉네임으로 가입하고 프로필/비밀번호를 변경한다."],
            ["FR-02", "상품", "누구나 상품을 조회하고, 판매자는 사진과 함께 상품을 등록·관리한다."],
            ["FR-03", "소통", "상품 상세에서 판매자와 상품별 1:1 메시지를 이용한다."],
            ["FR-04", "차단", "상품·사용자를 신고하고 누적 신고 또는 관리자 판단으로 제한한다."],
            ["FR-05", "송금", "구매 시 구매자 잔액이 감소하고 판매자 잔액이 같은 금액 증가한다."],
            ["FR-06", "검색", "상품명·설명으로 검색한다."],
            ["FR-07", "관리", "관리자가 사용자, 상품, 신고를 확인·관리한다."],
        ], [18 * mm, 30 * mm, 112 * mm], st),
        p("비기능 요구사항", st["h2"]),
        bullet("Python 3.11 이상에서 외부 패키지 없이 실행", st["body"]),
        bullet("계정 인증정보, 세션, 개인 메시지, 거래 잔액과 상품 무결성 보호", st["body"]),
        bullet("객체 단위 권한 검사와 동시 거래에서의 불변조건 보장", st["body"]),
        bullet("PC·모바일 핵심 기능 사용 가능, 오류 시 내부정보 미노출", st["body"]),
        p("주요 오용 사례", st["h2"]),
        table([
            ["공격/오용", "위험", "설계 반영"],
            ["ID 변경으로 타인 상품 수정", "수평 권한 상승", "seller_id를 서버와 SQL에서 모두 검사"],
            ["악성 사이트의 구매 POST", "CSRF", "모든 POST에 세션 CSRF 토큰"],
            ["상품·채팅에 script 저장", "저장형 XSS", "출력 escape + CSP"],
            ["동시에 같은 상품 구매", "중복 판매·포인트 불일치", "즉시 트랜잭션 + 조건부 UPDATE"],
            ["한 명이 신고 반복", "정상 대상 자동 차단", "신고자·대상 UNIQUE"],
        ], [38 * mm, 42 * mm, 80 * mm], st),
        PageBreak(),
    ])

    story.extend([
        p("03. 시스템 설계", st["h1"]),
        p("논리 구성", st["h2"]),
        table([
            ["브라우저", "HTTPS / POST + CSRF", "WSGI 애플리케이션", "매개변수 SQL", "SQLite"],
            ["HTML/CSS", "세션 쿠키", "인증·상품·채팅·신고·거래·관리", "트랜잭션", "데이터·감사로그"],
        ], [28 * mm, 31 * mm, 50 * mm, 27 * mm, 27 * mm], st),
        Spacer(1, 5 * mm),
        p("요청 처리 흐름", st["h2"]),
        table([
            ["1", "요청 경로·본문 크기 확인"], ["2", "세션 원문을 해시해 DB 세션 조회, 만료/정지 상태 확인"],
            ["3", "POST 요청의 CSRF 토큰을 상수 시간 비교"], ["4", "기능별 입력 형식·길이·허용값 검증"],
            ["5", "로그인·소유자·대화 참가자·관리자 권한 검사"], ["6", "바인딩 쿼리 및 필요한 경우 원자적 트랜잭션 수행"],
            ["7", "HTML escape 후 CSP와 보안 헤더를 포함해 응답"],
        ], [12 * mm, 145 * mm], st, header=False),
        p("데이터 모델", st["h2"]),
        table([
            ["테이블", "핵심 필드", "무결성/보안 제약"],
            ["users", "username, password_hash, balance, role, status", "아이디 UNIQUE, 잔액 범위, 역할·상태 허용 목록"],
            ["sessions", "token_hash, user_id, csrf_token, expires_at", "원문 토큰 미저장, 만료 인덱스"],
            ["products", "seller_id, buyer_id, price, status, moderation_status", "가격 범위, 판매·노출 상태 허용 목록"],
            ["messages", "product_id, sender_id, recipient_id, body", "상품별 1:1, 본문 길이, 자기 자신 수신 금지"],
            ["reports", "reporter_id, target_type, target_id, reason", "동일 신고자·대상 UNIQUE"],
            ["transfers", "sender_id, recipient_id, product_id, amount", "양수 금액, 자기 송금 금지"],
            ["audit_log", "user_id, event, target, ip_hash", "민감 본문·IP 원문 미기록"],
        ], [28 * mm, 72 * mm, 60 * mm], st),
        PageBreak(),
    ])

    story.extend([
        p("04. 구현", st["h1"]),
        p("모듈 구성", st["h2"]),
        table([
            ["파일", "책임"], ["tiny_market/app.py", "라우팅, 인증/인가, 상품·채팅·신고·거래·관리자 로직"],
            ["tiny_market/security.py", "비밀번호 해싱, 세션/CSRF 토큰, 폼 파싱·크기 제한"],
            ["tiny_market/db.py", "SQLite 연결, 스키마 초기화, 트랜잭션 경계"],
            ["tiny_market/views.py", "동적 데이터 이스케이프와 서버 렌더링 HTML"],
            ["tiny_market/schema.sql", "테이블·인덱스·CHECK/UNIQUE 제약"],
            ["tests/test_app.py", "정상·공격·권한·경계·거래 불변조건 테스트"],
        ], [50 * mm, 110 * mm], st),
        p("인증과 세션", st["h2"]),
        p("비밀번호는 128-bit 난수 salt와 PBKDF2-HMAC-SHA256 600,000회로 해시한다. 256-bit 세션 토큰은 쿠키에만 전달하고 DB에는 SHA-256 해시를 저장한다. 인증 성공 시 익명 세션을 폐기하고 새 토큰을 발급하여 세션 고정을 막는다. 쿠키에는 HttpOnly, SameSite=Strict, 8시간 수명을 적용하며 운영 환경에서는 Secure와 HSTS를 추가한다.", st["body"]),
        p("원자적 포인트 송금", st["h2"]),
        table([
            ["순서", "동일 BEGIN IMMEDIATE 트랜잭션에서 수행"],
            ["1", "상품이 visible + available이며 판매자와 구매자가 다른지 확인"],
            ["2", "balance >= price 조건으로 구매자 잔액 차감"],
            ["3", "status='available' 조건으로 상품을 sold로 변경"],
            ["4", "판매자 잔액 적립 및 transfers 원장 기록"],
            ["5", "어느 조건이든 실패하면 성공 처리하지 않음"],
        ], [20 * mm, 140 * mm], st),
        p("신고·자동 조치", st["h2"]),
        p("동일 사용자가 같은 대상을 반복 신고할 수 없도록 DB UNIQUE 제약을 적용했다. 서로 다른 사용자 3명이 상품을 신고하면 자동 숨김, 5명이 일반 사용자를 신고하면 세션을 폐기하고 계정을 정지한다. 관리자는 대시보드에서 자동 조치를 검토하고 상태를 전환할 수 있다.", st["body"]),
        PageBreak(),
    ])

    weaknesses = [
        ["초기 약점", "영향", "변경", "검증"],
        ["빠른/평문 비밀번호 저장", "DB 유출 시 계정 탈취", "salt + PBKDF2 600k", "서로 다른 해시·검증 테스트"],
        ["인증 후 기존 세션 유지", "세션 고정", "성공 시 세션 회전", "브라우저 가입 흐름"],
        ["CSRF 누락", "의도치 않은 삭제·구매", "모든 POST 토큰 비교", "무토큰 요청 403"],
        ["ID만으로 객체 수정", "IDOR", "소유권 2중 검사", "공격자 수정·삭제 403"],
        ["문자열 SQL", "SQL 삽입", "매개변수와 LIKE escape", "OR 1=1 검색"],
        ["사용자 입력 HTML 출력", "저장형 XSS", "HTML escape + CSP", "script 문자열 테스트"],
        ["거래 단계 분리", "이중 구매·잔액 오류", "단일 즉시 트랜잭션", "2회 구매 1회 송금"],
        ["무제한 로그인", "비밀번호 대입", "계정별 5회+IP별 20회 제한", "IP 우회 회귀 테스트"],
        ["원본 사진 저장", "위장·메타데이터 노출", "PNG/JPEG 전체 디코딩·재인코딩", "WebP 거부·메타데이터 제거"],
        ["채팅 사진 공개 URL", "제3자 사진 열람", "대화 참가자 권한 검사", "제3자·공개 경로 404"],
        ["반복 신고", "정상 사용자 차단", "신고자·대상 UNIQUE", "3명 신고 자동 숨김"],
        ["URL만 숨긴 관리자 기능", "권한 탈취", "role 서버 검사", "일반 사용자 403"],
        ["IP 원문 로그", "로그 개인정보 노출", "privacy hash", "감사 함수 검토"],
    ]
    story.extend([
        p("05. 보안 약점과 개선", st["h1"]),
        p("개발 과정에서 기능별로 ‘공격자가 입력·ID·순서·상태를 조작하면 무엇이 깨지는가’를 검토했다. 아래 표는 확인한 약점과 실제 변경, 검증 근거를 연결한다.", st["body"]),
        table(weaknesses, [36 * mm, 35 * mm, 50 * mm, 39 * mm], st),
        PageBreak(),
        p("05. 보안 약점과 개선 (계속)", st["h1"]),
        p("브라우저 보안 통제", st["h2"]),
        table([
            ["헤더/정책", "효과"],
            ["Content-Security-Policy", "self 이외 리소스와 script, object, base, frame 삽입을 기본 차단"],
            ["frame-ancestors 'none' + X-Frame-Options DENY", "clickjacking 방어"],
            ["X-Content-Type-Options nosniff", "MIME 추측 실행 방지"],
            ["Referrer-Policy no-referrer", "외부로 경로·검색어 유출 방지"],
            ["Permissions-Policy", "카메라·마이크·위치 권한 비활성화"],
            ["Cache-Control no-store", "인증 화면의 공유 캐시 저장 방지"],
        ], [58 * mm, 102 * mm], st),
        p("보안 의사결정과 잔여 위험", st["h2"]),
        bullet("실제 금융망 대신 교육용 내부 포인트를 사용하며 현금 가치가 없음을 README에 명시했다.", st["body"]),
        bullet("채팅은 서버 저장형이고 종단간 암호화가 아니므로 개인정보 전송 금지 문구를 표시했다.", st["body"]),
        bullet("표준 라이브러리 WSGI 서버는 로컬 검증용이다. 외부 배포에는 HTTPS 프록시와 운영 서버가 필요하다.", st["body"]),
        bullet("SQLite 단일 인스턴스를 전제로 하며 대규모 분산 환경에는 외부 DB·중앙 속도 제한이 필요하다.", st["body"]),
        p("에러·로그 처리", st["h2"]),
        p("예상하지 못한 예외는 서버 로그에만 기록하고 사용자에게는 일반화된 500 응답을 보낸다. 감사 로그에는 이벤트와 대상, 해시된 IP만 기록하며 비밀번호와 메시지 본문은 기록하지 않는다. 로그인 오류는 아이디 존재 여부를 구분하지 않는다.", st["body"]),
        PageBreak(),
    ])

    story.extend([
        p("06. 체크리스트와 테스트", st["h1"]),
        p("체크리스트 요약", st["h2"]),
        table([
            ["영역", "주요 확인 항목", "결과"],
            ["계정", "중복 방지, 강한 비밀번호, 세션 회전, 프로필 변경", "PASS"],
            ["상품", "공개 조회, 소유자 CRUD, 검색, 입력 경계", "PASS"],
            ["소통", "상품별 1:1 메시지와 대화 참가자 격리", "PASS"],
            ["신고", "중복 방지, 자동 숨김/정지, 관리자 검토", "PASS"],
            ["송금", "잔액 충분성, 자기 구매, 이중 구매, 원장", "PASS"],
            ["웹 보안", "CSRF, XSS, SQLi, IDOR, 보안 헤더", "PASS"],
            ["UI", "데스크톱·390px 모바일, 키보드 레이블", "PASS"],
        ], [28 * mm, 105 * mm, 27 * mm], st),
        p("자동 테스트 결과", st["h2"]),
        table([
            ["#", "테스트", "기대/결과"],
            ["1", "비밀번호 salt·검증", "동일 비밀번호 해시 상이, 검증 성공"],
            ["2", "보안 헤더·쿠키", "CSP/DENY/HttpOnly/SameSite 확인"],
            ["3", "CSRF", "토큰 없는 상태 변경 403"],
            ["4", "XSS·SQLi", "script escape, OR 1=1 데이터 미노출"],
            ["5", "소유권", "타인 상품 수정·삭제 403"],
            ["6", "구매 송금", "2회 요청에도 송금 원장 1개"],
            ["7", "잔액 부족", "상품 available·buyer NULL 유지"],
            ["8", "신고 누적", "서로 다른 3명 후 상품 hidden"],
            ["9", "관리자 권한", "일반 사용자 대시보드 403"],
            ["10", "1:1 메시지", "제3자 대화 화면에 본문 미노출"],
            ["11", "닉네임", "대소문자 무시 중복 등록 차단"],
            ["12", "사진 업로드", "정상 PNG 저장, 위장 파일 거부"],
            ["13", "관리자 삭제", "신고 상품만 삭제, 신고 resolved"],
            ["14", "회원가입 닉네임", "고유 닉네임 저장·세션 발급"],
            ["15", "사용자 차단", "상품 비노출, 채팅·구매 거부"],
            ["16", "관리자 통합 관리", "신고 처리·차단 해제·계정 정지"],
            ["17", "필수 상품 사진", "사진 없는 새 상품 등록 거부"],
            ["18", "WebP 사진", "신규 WebP 업로드 거부"],
            ["19", "채팅 사진·알림", "사진 저장, 미확인 1→열람 후 0"],
            ["20", "다중 사진", "상품·채팅 10장 허용, 11장 거부"],
            ["21", "위장 WebP", "PNG로 위장한 WebP도 디코더 전에 거부"],
            ["22", "채팅 사진 권한", "참가자만 조회, 제3자·공개 경로 404"],
            ["23", "로그인 IP 제한", "아이디를 바꿔도 21번째 요청 429"],
            ["24", "활동 정지", "로그인·읽기 허용, 거래·전송 거부"],
            ["25", "배포 상태 확인", "세션 생성 없이 200 응답"],
            ["26", "관리자 최초 생성", "재실행해도 관리자 한 명 유지"],
            ["27", "한 글자 일치 검색", "검색어 글자 하나 이상 포함된 상품명만 표시"],
        ], [10 * mm, 60 * mm, 90 * mm], st),
        p("실행 결과: 27 tests / 27 passed / 0 failed / 0 errors", st["callout"]),
        PageBreak(),
    ])

    story.extend([
        p("06. 브라우저·유지보수 검증", st["h1"]),
        p("브라우저 수동 검증", st["h2"]),
        table([
            ["단계", "관찰 결과"],
            ["홈", "CSP 적용 상태에서 CSS·SVG 정상, 검색·가입 링크 접근 가능"],
            ["회원가입", "필드 레이블/비밀번호 규칙 표시, 성공 후 홈에서 인증 메뉴 표시"],
            ["세션", "가입 POST 응답에서 새 인증 세션이 유지되어 세션 회전 로직 정상"],
            ["상품 등록", "허용 카테고리·상태 선택, 상세 페이지와 판매자 관리 버튼 표시"],
            ["모바일", "초기 390px 검수에서 가로 넘침 발견 → 메뉴 줄바꿈·폭 제한 → 재검증"],
        ], [34 * mm, 126 * mm], st),
        p("유지보수 계획", st["h2"]),
        bullet("인증·세션·거래·관리자 변경 시 27개 전체 회귀 테스트 실행", st["body"]),
        bullet("DB 스키마 변경 전 백업, 명시적 마이그레이션, 복원 테스트 도입", st["body"]),
        bullet("운영 전 TLS, 프록시 IP 신뢰 경계, DB 권한, 백업 암호화, 요청 속도 제한 점검", st["body"]),
        bullet("신고 오탐·미탐과 로그인 차단 결과를 관찰해 임계값 조정", st["body"]),
        bullet("Python 보안 업데이트 적용 후 자동 테스트와 브라우저 smoke test 반복", st["body"]),
        p("유지보수 환류 사례", st["h2"]),
        p("브라우저 점검에서 모바일 상세 화면의 가로 넘침을 발견했다. 설계 단계의 반응형 요구로 돌아가 760px 이하에서 내비게이션 줄바꿈, 상세 카드 최소 폭 0, 제목 줄바꿈을 적용한 뒤 390px 뷰포트의 실제 폭(document 390px, detail 358px)을 확인했다. 이는 구현→테스트→요구/설계 수정→재구현의 유지보수 순환을 실제 수행한 사례다.", st["body"]),
        PageBreak(),
    ])

    story.extend([
        p("07. AI 활용과 결론", st["h1"]),
        p("AI 도구 활용", st["h2"]),
        table([
            ["단계", "Codex 활용", "사람/도구 검증"],
            ["요구사항", "수업 PDF 36쪽 추출·최소 요구 분류", "원본 35쪽 시각 확인"],
            ["설계", "위협 모델·DB 제약·권한 모델 초안", "요구사항 추적표와 체크리스트"],
            ["구현", "모듈·화면·보안 로직 작성", "컴파일 및 코드 경로 검토"],
            ["테스트", "공격·경계·권한 테스트 생성", "27개 자동 테스트 전부 실행"],
            ["UI", "실제 브라우저 상호작용·반응형 점검", "화면 캡처와 DOM 폭 확인"],
            ["문서", "README·보안표·PDF 제작", "PDF 렌더링·텍스트·페이지 검수"],
        ], [28 * mm, 70 * mm, 62 * mm], st),
        p("AI 사용 시 보안 원칙", st["h2"]),
        p("AI가 만든 코드는 정답으로 간주하지 않았다. 보안 통제마다 실패해야 하는 공격 테스트를 함께 만들고, DB 제약과 서버 권한 검사를 중복 적용했으며, 브라우저에서 실제 세션 상태와 화면을 확인했다. 특히 가입 응답의 세션 쿠키 우선순위 문제와 모바일 레이아웃 문제는 검증 단계에서 발견해 수정했다. 최종 판단과 제출 책임은 작성자에게 있다.", st["body"]),
        p("결론", st["h2"]),
        p("Tweety는 수업 자료의 가입, 상품, 소통, 차단, 송금, 검색, 관리자 요구를 모두 충족한다. 비밀번호·세션·입력·권한·상태·오류·로그를 한 개발 주기 안에서 함께 다뤘으며, 자동 테스트와 수동 브라우저 검증으로 주요 약점의 제거 여부를 확인했다. 남은 운영 위험은 README와 보안 정책에 명시해 배포 단계의 후속 통제로 연결했다.", st["body"]),
        Spacer(1, 8 * mm),
        p("Public GitHub Repository", st["h2"]),
        p(args.repo_url, st["callout"]),
        Spacer(1, 20 * mm),
        p("— END OF REPORT —", st["center"]),
    ])

    decorator = page_decorator(args.student, font)
    doc.build(story, onFirstPage=decorator, onLaterPages=decorator)


def parse_args():
    parser = argparse.ArgumentParser(description="Tweety 제출용 PDF 보고서 생성")
    parser.add_argument("--student", required=True)
    parser.add_argument("--class-number", required=True)
    parser.add_argument("--last4", required=True)
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"\d{2}", args.class_number):
        parser.error("--class-number는 두 자리 숫자여야 합니다 (예: 01)")
    if not re.fullmatch(r"\d{4}", args.last4):
        parser.error("--last4는 전화번호 뒤 4자리여야 합니다")
    return args


if __name__ == "__main__":
    build(parse_args())
