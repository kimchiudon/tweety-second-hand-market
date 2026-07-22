from __future__ import annotations

from html import escape


CATEGORIES = {
    "digital": "디지털",
    "fashion": "패션",
    "home": "생활/가구",
    "books": "도서",
    "sports": "스포츠",
    "other": "기타",
}

CONDITIONS = {
    "new": "새 상품",
    "like_new": "거의 새것",
    "good": "사용감 적음",
    "fair": "사용감 있음",
}


def e(value: object) -> str:
    return escape(str(value), quote=True)


def money(value: int) -> str:
    return f"{value:,}원"


def product_image(product) -> str:
    filenames = product.get("image_filenames", []) if isinstance(product, dict) else []
    filename = filenames[0] if filenames else (product["image_filename"] if "image_filename" in product.keys() else None)
    return f"/uploads/{e(filename)}" if filename else "/static/product-placeholder.svg"


def layout(title: str, content: str, *, user=None, csrf_token: str = "") -> str:
    if user:
        suspended = user.get("status") != "active"
        unread = int(user.get("unread_count", 0) or 0)
        hidden = "" if unread else " hidden"
        chat_label = f'채팅 <span id="unread-badge" class="notification-badge" aria-label="읽지 않은 메시지 {unread}개"{hidden}>{unread}</span>'
        auth = f"""
        {('<span class="account-status">활동 정지</span>' if suspended else '<a href="/products/new">판매하기</a>')}
        <a href="/chat">{chat_label}</a>
        <a href="/my">내 상점</a>
        {'<a href="/admin">관리</a>' if user.get('role') == 'admin' else ''}
        <form method="post" action="/logout" class="inline">
          <input type="hidden" name="csrf_token" value="{e(csrf_token)}">
          <button class="link-button" type="submit">로그아웃</button>
        </form>"""
    else:
        auth = '<a href="/login">로그인</a><a class="button small" href="/register">회원가입</a>'
        unread = 0
    notification = f'<a id="notification-alert" class="notification-alert" href="/chat" role="status"{("" if unread else " hidden")}>새 채팅 메시지가 <span id="notification-count">{unread}</span>개 있습니다. 확인하기 →</a>' if user else ""
    restriction = '<div class="restriction-banner" role="status"><strong>계정 활동이 정지되었습니다.</strong> 로그인과 내역·기존 채팅 조회는 가능하지만 판매·구매·메시지 전송은 제한됩니다.</div>' if user and user.get("status") != "active" else ""
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{e(title)} | Tweety</title>
  <link rel="stylesheet" href="/static/style.css">
  {'<script src="/static/notifications.js" defer></script>' if user else ''}
</head>
<body>
  <header class="site-header">
    <nav class="container nav" aria-label="주요 메뉴">
      <a class="brand" href="/"><span class="brand-mark" aria-hidden="true">●</span>Tweety</a>
      <div class="nav-links">{auth}</div>
    </nav>
  </header>
  <main class="container">{restriction}{notification}{content}</main>
  <footer class="container footer"><strong>Tweety</strong> · 아주 작고 안전한 중고 거래 플랫폼 · 2026</footer>
</body>
</html>"""


def errors(items: list[str]) -> str:
    if not items:
        return ""
    rows = "".join(f"<li>{e(item)}</li>" for item in items)
    return f'<div class="alert error" role="alert"><strong>입력 내용을 확인해 주세요.</strong><ul>{rows}</ul></div>'


def home(products, *, query: str, user=None, csrf_token: str = "") -> str:
    cards = []
    for product in products:
        status = '<span class="badge sold">판매 완료</span>' if product["status"] == "sold" else ""
        cards.append(f"""
        <article class="card">
          <a href="/products/{product['id']}" class="product-image"><img src="{product_image(product)}" alt="{e(product['title'])} 상품 이미지"></a>
          <div class="card-top"><span class="badge">{e(CATEGORIES[product['category']])}</span>{status}</div>
          <h2><a href="/products/{product['id']}">{e(product['title'])}</a></h2>
          <p class="price">{money(product['price'])}</p>
          <p class="muted">{e(CONDITIONS[product['item_condition']])} · 판매자 {e(product['seller_name'])}</p>
        </article>""")
    listing = "".join(cards) if cards else '<div class="empty">조건에 맞는 상품이 없습니다.</div>'
    content = f"""
    <section class="hero">
      <div class="hero-copy"><p class="eyebrow">Tiny보다 더 작은 중고 장터</p>
      <h1>작은 물건의<br><em>다음 이야기.</em></h1>
      <p>노란 오리 Tweety와 함께, 필요한 만큼만 가볍고 안전하게 거래하세요.</p></div>
      <img class="hero-mascot" src="/static/tweety-mascot.png" alt="작은 장바구니 옆에 선 Tweety 노란 오리 마스코트">
    </section>
    <form class="search" method="get" action="/">
      <label class="sr-only" for="q">상품 검색</label>
      <input id="q" name="q" maxlength="80" value="{e(query)}" placeholder="상품명이나 설명을 검색하세요">
      <button type="submit">검색</button>
    </form>
    <div class="section-title"><h2>최근 상품</h2><span>{len(products)}개</span></div>
    <section class="grid">{listing}</section>"""
    return layout("중고 거래", content, user=user, csrf_token=csrf_token)


def auth_page(kind: str, *, form: dict | None = None, error_items: list[str] | None = None, csrf_token: str = "") -> str:
    form = form or {}
    is_register = kind == "register"
    heading = "새 계정 만들기" if is_register else "다시 만나서 반가워요"
    submit = "회원가입" if is_register else "로그인"
    password_help = '<p class="help">10자 이상, 영문 대·소문자와 숫자를 포함해 주세요.</p>' if is_register else ""
    nickname_field = f'''<label for="nickname">닉네임</label><input id="nickname" name="nickname" required minlength="2" maxlength="20" pattern="[A-Za-z0-9_가-힣]+" autocomplete="nickname" value="{e(form.get('nickname', ''))}"><p class="help">판매자명으로 표시되며 다른 사용자와 중복될 수 없습니다.</p>''' if is_register else ""
    switch = '이미 계정이 있나요? <a href="/login">로그인</a>' if is_register else '아직 계정이 없나요? <a href="/register">회원가입</a>'
    content = f"""
    <section class="form-shell narrow">
      <p class="eyebrow">Tweety</p><h1>{heading}</h1>
      {errors(error_items or [])}
      <form method="post" action="/{kind}" novalidate>
        <input type="hidden" name="csrf_token" value="{e(csrf_token)}">
        <label for="username">아이디</label>
        <input id="username" name="username" required minlength="3" maxlength="24" pattern="[A-Za-z0-9_]+" autocomplete="username" value="{e(form.get('username', ''))}">
        {nickname_field}
        <label for="password">비밀번호</label>
        <input id="password" type="password" name="password" required minlength="10" maxlength="128" autocomplete="{'new-password' if is_register else 'current-password'}">
        {password_help}<button class="full" type="submit">{submit}</button>
      </form>
      <p class="switch">{switch}</p>
    </section>"""
    return layout(submit, content)


def product_form(*, product=None, error_items=None, csrf_token: str, user=None) -> str:
    values = dict(product) if product else {}
    editing = "id" in values
    category_options = "".join(
        f'<option value="{e(value)}"{(" selected" if values.get("category") == value else "")}>{e(label)}</option>'
        for value, label in CATEGORIES.items()
    )
    condition_options = "".join(
        f'<option value="{e(value)}"{(" selected" if values.get("item_condition") == value else "")}>{e(label)}</option>'
        for value, label in CONDITIONS.items()
    )
    action = f"/products/{product['id']}/edit" if editing else "/products/new"
    heading = "상품 정보 수정" if editing else "판매할 상품 등록"
    existing_images = values.get("image_filenames", []) or ([values.get("image_filename")] if values.get("image_filename") else [])
    current_image = (f'<div class="form-image-grid">{"".join(f"<img class=\"form-image-preview\" src=\"/uploads/{e(filename)}\" alt=\"현재 상품 사진\">" for filename in existing_images)}</div><p class="help">현재 {len(existing_images)}장입니다. 아래에서 새 사진을 고르면 모두 교체됩니다.</p>') if editing and existing_images else ""
    required = "" if editing else " required"
    content = f"""
    <section class="form-shell">
      <p class="eyebrow">판매자 도구</p><h1>{heading}</h1>
      {errors(error_items or [])}
      <form method="post" action="{action}" enctype="multipart/form-data" novalidate>
        <input type="hidden" name="csrf_token" value="{e(csrf_token)}">
        <label for="title">상품명</label>
        <input id="title" name="title" required minlength="2" maxlength="80" value="{e(values.get('title', ''))}">
        <div class="two-col"><div><label for="price">가격 (원)</label>
        <input id="price" name="price" type="number" min="0" max="100000000" required value="{e(values.get('price', ''))}"></div>
        <div><label for="category">카테고리</label><select id="category" name="category" required>{category_options}</select></div></div>
        <label for="item_condition">상품 상태</label><select id="item_condition" name="item_condition" required>{condition_options}</select>
        <label for="image">상품 사진</label>
        {current_image}
        <input id="image" name="images" type="file" accept=".png,.jpg,.jpeg,image/png,image/jpeg" multiple data-max-files="10" data-error-target="product-image-error" aria-describedby="product-image-help product-image-error"{required}>
        <p id="product-image-help" class="help">PNG/JPEG 사진을 최대 10장까지 선택할 수 있습니다. 서버가 안전하게 다시 저장하며 위치정보 등 메타데이터는 제거됩니다.</p><p id="product-image-error" class="alert error compact" role="alert" hidden>사진은 최대 10장까지만 선택할 수 있습니다. 다시 선택해 주세요.</p>
        <label for="description">설명</label>
        <textarea id="description" name="description" required minlength="10" maxlength="2000" rows="8">{e(values.get('description', ''))}</textarea>
        <p class="help">연락처, 주소 등 개인정보는 상품 설명에 작성하지 마세요.</p>
        <button type="submit">{heading}</button>
      </form>
    </section>"""
    return layout(heading, content, user=user, csrf_token=csrf_token)


def product_detail(product, *, user=None, csrf_token: str = "") -> str:
    owner = user and user["id"] == product["seller_id"]
    available = product["status"] == "available"
    actions = ""
    if owner and user.get("status") == "active":
        actions = f"""<div class="actions"><a class="button" href="/products/{product['id']}/edit">수정</a>
        <form method="post" action="/products/{product['id']}/delete">
          <input type="hidden" name="csrf_token" value="{e(csrf_token)}"><button class="danger" type="submit">삭제</button>
        </form></div>"""
    elif owner:
        actions = f"""<div class="restriction-note">활동 정지 중에는 상품을 수정하거나 판매할 수 없습니다.</div>
        <form method="post" action="/products/{product['id']}/delete"><input type="hidden" name="csrf_token" value="{e(csrf_token)}"><button class="danger full" type="submit">상품 삭제</button></form>"""
    elif user and user.get("status") == "active" and available:
        actions = f"""<a class="button full center" href="/products/{product['id']}/checkout">포인트 송금 확인하기</a>
          <p class="help">실제 현금 결제가 아닌 교육용 포인트 거래입니다. 확인 화면을 거쳐 판매자에게 송금됩니다.</p>"""
    elif user and user.get("status") != "active" and available:
        actions = '<div class="restriction-note">활동 정지 중에는 상품을 구매할 수 없습니다.</div>'
    elif not user and available:
        actions = '<a class="button full center" href="/login">로그인하고 구매하기</a>'
    badge = '<span class="badge sold">판매 완료</span>' if not available else '<span class="badge available">판매 중</span>'
    report = ""
    if user and not owner:
        report = f'<a class="text-danger" href="/report/product/{product["id"]}">이 상품 신고</a> · <a href="/chat/{product["id"]}">판매자와 1:1 채팅</a> · <form class="inline" method="post" action="/block/{product["seller_id"]}/toggle"><input type="hidden" name="csrf_token" value="{e(csrf_token)}"><button class="link-button text-danger" type="submit">판매자 차단</button></form>'
    gallery_items = product.get("image_filenames", []) if isinstance(product, dict) else []
    gallery = "".join(f'<img class="detail-image" src="/uploads/{e(filename)}" alt="{e(product["title"])} 상품 사진 {index}">' for index, filename in enumerate(gallery_items, 1)) or f'<img class="detail-image" src="{product_image(product)}" alt="{e(product["title"])} 상품 이미지">'
    content = f"""
    <article class="detail">
      <div class="detail-main"><div class="detail-gallery">{gallery}</div><div class="card-top"><span class="badge">{e(CATEGORIES[product['category']])}</span>{badge}</div>
      <h1>{e(product['title'])}</h1><p class="price big">{money(product['price'])}</p>
      <p class="muted">{e(CONDITIONS[product['item_condition']])} · <a href="/users/{product['seller_id']}">판매자 {e(product['seller_name'])}</a></p>
      <hr><div class="description">{e(product['description']).replace(chr(10), '<br>')}</div></div>
      <aside class="purchase-box"><h2>거래하기</h2>{actions}<p class="help">{report}</p></aside>
    </article>"""
    return layout(product["title"], content, user=user, csrf_token=csrf_token)


def checkout_page(product, *, user, csrf_token: str) -> str:
    remaining = user["balance"] - product["price"]
    enough = remaining >= 0
    submit = f'''<form method="post" action="/products/{product['id']}/purchase">
      <input type="hidden" name="csrf_token" value="{e(csrf_token)}">
      <button class="full" type="submit">{money(product['price'])} 포인트 송금하고 구매 확정</button>
    </form>''' if enough else '<a class="button full center disabled" href="/my" aria-disabled="true">포인트 잔액이 부족합니다</a>'
    content = f'''<section class="checkout-shell">
      <a class="back-link" href="/products/{product['id']}">← 상품으로 돌아가기</a>
      <div class="checkout-card">
        <p class="eyebrow">Tweety 안전 송금</p><h1>송금 내용을 확인해 주세요</h1>
        <div class="checkout-product"><img src="{product_image(product)}" alt="{e(product['title'])} 상품 이미지"><div><strong>{e(product['title'])}</strong><p class="muted">받는 사람 · {e(product['seller_name'])}</p></div></div>
        <dl class="payment-summary"><div><dt>보유 포인트</dt><dd>{money(user['balance'])}</dd></div><div><dt>송금 금액</dt><dd>− {money(product['price'])}</dd></div><div class="total"><dt>{'송금 후 잔액' if enough else '부족한 포인트'}</dt><dd>{money(remaining) if enough else money(-remaining)}</dd></div></dl>
        <div class="demo-notice"><strong>교육용 포인트 송금</strong><p>카드·계좌 또는 실제 현금을 사용하지 않습니다. 버튼을 누르면 구매자의 포인트가 판매자에게 한 번만 이전됩니다.</p></div>
        {submit}<p class="help center">판매 완료 후 같은 상품에 대한 중복 송금은 서버에서 차단됩니다.</p>
      </div>
    </section>'''
    return layout("송금 확인", content, user=user, csrf_token=csrf_token)


def my_page(selling, bought, transfers, blocked_users, *, user, csrf_token: str) -> str:
    def rows(items, role: str) -> str:
        if not items:
            return '<p class="empty compact">아직 상품이 없습니다.</p>'
        return "".join(
            f'<a class="list-row" href="/products/{item["id"]}"><span>{e(item["title"])}</span><strong>{money(item["price"])}</strong><span class="badge">{e("판매 완료" if item["status"] == "sold" else "판매 중")}</span></a>'
            for item in items
        )
    transfer_rows = "".join(
        f'<tr><td>{e(t["created_at"])}</td><td>{e(t["title"] or "삭제된 상품")}</td><td>{"보낸 금액" if t["sender_id"] == user["id"] else "받은 금액"}</td><td><strong>{"−" if t["sender_id"] == user["id"] else "+"}{money(t["amount"])}</strong></td><td>{e(t["recipient_name"] if t["sender_id"] == user["id"] else t["sender_name"])}</td></tr>'
        for t in transfers
    ) or '<tr><td colspan="5">아직 송금 내역이 없습니다.</td></tr>'
    blocked_rows = "".join(
        f'<tr><td><a href="/users/{b["user_id"]}">{e(b["nickname"])}</a></td><td>{e(b["created_at"])}</td><td><form method="post" action="/block/{b["user_id"]}/toggle"><input type="hidden" name="csrf_token" value="{e(csrf_token)}"><button class="small" type="submit">차단 해제</button></form></td></tr>'
        for b in blocked_users
    ) or '<tr><td colspan="3">차단한 사용자가 없습니다.</td></tr>'
    content = f"""<section class="page-head"><p class="eyebrow">내 상점</p><h1>{e(user['nickname'])}님의 거래</h1><p class="muted">로그인 아이디: {e(user['username'])}</p><p class="balance">보유 포인트 <strong>{money(user['balance'])}</strong></p><p class="help">상품 구매 시 결제 금액이 판매자에게 자동 송금됩니다.</p><p>{e(user.get('bio') or '아직 소개글이 없습니다.')}</p><a class="button small" href="/profile/edit">프로필·비밀번호 변경</a></section>
    <section class="panel"><div class="section-title"><h2>판매 상품</h2><span>{len(selling)}개</span></div>{rows(selling, 'selling')}</section>
    <section class="panel"><div class="section-title"><h2>구매 상품</h2><span>{len(bought)}개</span></div>{rows(bought, 'bought')}</section>
    <section class="panel table-wrap"><h2>포인트 송금 내역</h2><table><thead><tr><th>일시</th><th>상품</th><th>구분</th><th>금액</th><th>상대방</th></tr></thead><tbody>{transfer_rows}</tbody></table></section>
    <section class="panel table-wrap"><h2>차단한 사용자</h2><table><thead><tr><th>닉네임</th><th>차단일</th><th>관리</th></tr></thead><tbody>{blocked_rows}</tbody></table></section>"""
    return layout("내 상점", content, user=user, csrf_token=csrf_token)


def profile_page(profile, products, *, is_blocked=False, user=None, csrf_token: str = "") -> str:
    cards = "".join(f'<a class="list-row" href="/products/{p["id"]}"><span>{e(p["title"])}</span><strong>{money(p["price"])}</strong></a>' for p in products)
    report = ""
    if user and user["id"] != profile["id"]:
        block_label = "차단 해제" if is_blocked else "사용자 차단"
        report = f'<div class="actions"><a class="text-danger" href="/report/user/{profile["id"]}">사용자 신고</a><form method="post" action="/block/{profile["id"]}/toggle"><input type="hidden" name="csrf_token" value="{e(csrf_token)}"><button class="danger" type="submit">{block_label}</button></form></div>'
    content = f'<section class="page-head"><p class="eyebrow">사용자 프로필</p><h1>{e(profile["nickname"])}</h1><p>{e(profile["bio"] or "소개글이 없습니다.")}</p>{report}</section><section class="panel"><div class="section-title"><h2>판매 상품</h2><span>{len(products)}개</span></div>{cards or "<p class=\"empty compact\">판매 중인 상품이 없습니다.</p>"}</section>'
    return layout(f'{profile["nickname"]} 프로필', content, user=user, csrf_token=csrf_token)


def profile_edit(*, user, csrf_token: str, error_items=None) -> str:
    content = f'''<section class="form-shell"><p class="eyebrow">계정 관리</p><h1>프로필·비밀번호 변경</h1>{errors(error_items or [])}
    <form method="post" action="/profile/edit"><input type="hidden" name="csrf_token" value="{e(csrf_token)}"><label for="nickname">닉네임</label><input id="nickname" name="nickname" required minlength="2" maxlength="20" value="{e(user.get("nickname", ""))}"><p class="help">판매자명으로 표시되며 중복될 수 없습니다.</p><label for="bio">소개글</label><textarea id="bio" name="bio" maxlength="300" rows="5">{e(user.get("bio", ""))}</textarea><label for="current_password">현재 비밀번호</label><input id="current_password" type="password" name="current_password" required autocomplete="current-password"><label for="new_password">새 비밀번호 (변경할 때만)</label><input id="new_password" type="password" name="new_password" maxlength="128" autocomplete="new-password"><button type="submit">변경사항 저장</button></form></section>'''
    return layout("프로필 변경", content, user=user, csrf_token=csrf_token)


def chat_inbox(conversations, *, user, csrf_token: str) -> str:
    rows_list = []
    for conversation in conversations:
        badge = f'<span class="notification-badge">{conversation["unread_count"]}</span>' if conversation["unread_count"] else ""
        status = '<span class="badge sold conversation-status">판매 완료</span>' if conversation["product_status"] == "sold" else '<span class="badge available conversation-status">판매 중</span>'
        rows_list.append(f'<a class="list-row" href="/chat/{conversation["product_id"]}/{conversation["counterpart_id"]}"><span><strong>{e(conversation["counterpart_name"])}</strong> {badge}<br><small>{e(conversation["title"])}</small> {status}</span><span>{e(conversation["last_at"])}</span></a>')
    rows = "".join(rows_list) or '<p class="empty compact">아직 상품 문의가 없습니다. 상품 상세 페이지에서 판매자와 대화를 시작할 수 있습니다.</p>'
    content = f'<section class="page-head"><p class="eyebrow">1:1 채팅</p><h1>상품 문의</h1><p>상품별 판매자·구매 희망자 간 대화만 표시됩니다.</p></section><section class="panel">{rows}</section>'
    return layout("1:1 채팅", content, user=user, csrf_token=csrf_token)


def chat_page(messages, product, counterpart, *, user, csrf_token: str) -> str:
    heading = f'{e(counterpart["nickname"])}님과의 대화'
    message_parts = []
    for message in reversed(messages):
        filenames = (message["image_filenames"] or message["image_filename"] or "").split("|")
        filenames = [filename for filename in filenames if filename]
        image = '<div class="chat-image-grid">' + "".join(f'<a href="/chat-uploads/{e(filename)}" target="_blank" rel="noopener"><img class="chat-image" src="/chat-uploads/{e(filename)}" alt="채팅으로 보낸 사진 {index}"></a>' for index, filename in enumerate(filenames, 1)) + '</div>' if filenames else ""
        message_parts.append(f'<article class="message-row"><strong>{e(message["sender_name"])}</strong><time>{e(message["created_at"])}</time>{image}<p>{e(message["body"])}</p></article>')
    message_rows = "".join(message_parts) or '<p class="empty compact">첫 메시지를 남겨보세요.</p>'
    composer = f'''<form method="post" action="/chat/send" enctype="multipart/form-data"><input type="hidden" name="csrf_token" value="{e(csrf_token)}"><input type="hidden" name="product_id" value="{product["id"]}"><input type="hidden" name="counterpart_id" value="{counterpart["id"]}"><label for="body">메시지</label><textarea id="body" name="body" maxlength="500" rows="3" placeholder="메시지나 사진 중 하나만 보내도 됩니다. 개인정보는 보내지 마세요."></textarea><label for="chat-image">사진 첨부</label><input id="chat-image" name="images" type="file" accept=".png,.jpg,.jpeg,image/png,image/jpeg" multiple data-max-files="10" data-error-target="chat-image-error" aria-describedby="chat-image-help chat-image-error"><p id="chat-image-help" class="help">PNG/JPEG 사진을 최대 10장까지 선택할 수 있습니다. 서버가 안전하게 다시 저장하고 메타데이터를 제거합니다.</p><p id="chat-image-error" class="alert error compact" role="alert" hidden>사진은 최대 10장까지만 선택할 수 있습니다. 다시 선택해 주세요.</p><button type="submit">메시지 보내기</button></form>''' if user.get("status") == "active" else '<div class="restriction-note">활동 정지 중에는 기존 대화를 읽을 수 있지만 새 메시지는 보낼 수 없습니다.</div>'
    content = f'''<section class="page-head"><p class="eyebrow">상품 1:1 채팅</p><h1>{heading}</h1><p><a href="/products/{product["id"]}">문의 상품: {e(product["title"])}</a></p></section><section class="panel chat"><div class="messages">{message_rows}</div>{composer}</section>'''
    return layout(heading, content, user=user, csrf_token=csrf_token)


def report_page(target_type: str, target, *, user, csrf_token: str, error_items=None) -> str:
    label = target["title"] if target_type == "product" else target["nickname"]
    content = f'''<section class="form-shell narrow"><p class="eyebrow">안전 거래</p><h1>{e(label)} 신고</h1>{errors(error_items or [])}<p>관리자가 검토할 수 있도록 구체적인 사유를 작성해 주세요.</p><form method="post"><input type="hidden" name="csrf_token" value="{e(csrf_token)}"><label for="reason">신고 사유</label><textarea id="reason" name="reason" minlength="10" maxlength="500" required rows="6"></textarea><button class="danger" type="submit">신고 접수</button></form></section>'''
    return layout("신고", content, user=user, csrf_token=csrf_token)


def admin_page(users, products, reports, blocks, *, user, csrf_token: str) -> str:
    def user_action(u) -> str:
        if u["role"] == "admin":
            return '<span class="muted">관리자 보호</span>'
        label = "활동 정지 해제" if u["status"] == "suspended" else "계정 활동 정지"
        css = "small" if u["status"] == "suspended" else "small danger"
        return f'<form method="post" action="/admin/user/{u["id"]}/toggle"><input type="hidden" name="csrf_token" value="{e(csrf_token)}"><button class="{css}" type="submit">{label}</button></form>'

    user_rows = "".join(f'''<tr><td>{e(u["username"])}</td><td>{e(u["nickname"])}</td><td>{e(u["role"])}</td><td>{e(u["status"])}</td><td>{money(u["balance"])}</td><td>{u["user_report_count"]}회<br><small>{e(u["open_report_reasons"] or "-")}</small></td><td>{user_action(u)}</td></tr>''' for u in users)
    product_rows = "".join(f'''<tr><td><a href="/products/{p["id"]}">{e(p["title"])}</a></td><td>{e(p["seller_name"])}</td><td>{p["report_count"]}회<br><small>{e(p["report_reasons"])}</small></td><td>{e(p["moderation_status"])}</td><td><form method="post" action="/admin/product/{p["id"]}/delete"><input type="hidden" name="csrf_token" value="{e(csrf_token)}"><button class="small danger" type="submit">신고 상품 삭제</button></form></td></tr>''' for p in products) or '<tr><td colspan="5">신고된 상품이 없습니다.</td></tr>'
    report_rows = "".join(f'''<tr><td>{e(r["target_type"])} · {e(r["target_name"] or "삭제된 대상")}</td><td>{e(r["reporter_name"])}</td><td>{e(r["reason"])}</td><td>{e(r["status"])}</td><td><div class="actions"><form method="post" action="/admin/report/{r["id"]}/resolve"><input type="hidden" name="csrf_token" value="{e(csrf_token)}"><button class="small" type="submit">처리 완료</button></form><form method="post" action="/admin/report/{r["id"]}/dismiss"><input type="hidden" name="csrf_token" value="{e(csrf_token)}"><button class="small" type="submit">기각</button></form><form method="post" action="/admin/report/{r["id"]}/reopen"><input type="hidden" name="csrf_token" value="{e(csrf_token)}"><button class="small" type="submit">재검토</button></form></div></td></tr>''' for r in reports) or '<tr><td colspan="5">신고 기록이 없습니다.</td></tr>'
    block_rows = "".join(f'''<tr><td>{e(b["blocker_name"])}</td><td>{e(b["blocked_name"])}</td><td>{e(b["created_at"])}</td><td><form method="post" action="/admin/block/{b["id"]}/delete"><input type="hidden" name="csrf_token" value="{e(csrf_token)}"><button class="small" type="submit">관리자 해제</button></form></td></tr>''' for b in blocks) or '<tr><td colspan="4">차단 기록이 없습니다.</td></tr>'
    content = f'''<section class="page-head"><p class="eyebrow">관리자</p><h1>플랫폼 관리</h1><p>사용자 신고, 차단 관계, 계정 활동 상태를 이곳에서 검토하고 조치할 수 있습니다.</p></section><section class="panel table-wrap"><h2>사용자·활동 정지</h2><table><thead><tr><th>아이디</th><th>닉네임</th><th>역할</th><th>상태</th><th>잔액</th><th>사용자 신고</th><th>관리</th></tr></thead><tbody>{user_rows}</tbody></table></section><section class="panel table-wrap"><h2>신고된 상품</h2><table><thead><tr><th>상품</th><th>판매자</th><th>신고</th><th>노출</th><th>관리</th></tr></thead><tbody>{product_rows}</tbody></table></section><section class="panel table-wrap"><h2>신고 처리</h2><table><thead><tr><th>대상</th><th>신고자</th><th>사유</th><th>상태</th><th>처리</th></tr></thead><tbody>{report_rows}</tbody></table></section><section class="panel table-wrap"><h2>사용자 차단 관계</h2><table><thead><tr><th>차단한 사용자</th><th>차단된 사용자</th><th>차단일</th><th>관리</th></tr></thead><tbody>{block_rows}</tbody></table></section>'''
    return layout("관리자", content, user=user, csrf_token=csrf_token)


def message_page(title: str, message: str, *, status: int = 400, user=None, csrf_token: str = "") -> str:
    content = f'<section class="message"><p class="status-code">{status}</p><h1>{e(title)}</h1><p>{e(message)}</p><a class="button" href="/">홈으로 돌아가기</a></section>'
    return layout(title, content, user=user, csrf_token=csrf_token)
