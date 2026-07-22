(() => {
  const badge = document.getElementById("unread-badge");
  const alert = document.getElementById("notification-alert");
  const count = document.getElementById("notification-count");
  if (!badge || !alert || !count) return;

  const refresh = async () => {
    try {
      const response = await fetch("/api/unread", { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) return;
      const value = Math.max(0, Number((await response.json()).unread) || 0);
      badge.textContent = String(value);
      badge.setAttribute("aria-label", `읽지 않은 메시지 ${value}개`);
      count.textContent = String(value);
      badge.hidden = value === 0;
      alert.hidden = value === 0;
    } catch (_) {
      // 네트워크가 잠시 끊겨도 기존 화면과 채팅 기능은 그대로 유지한다.
    }
  };

  // 서버 렌더링 이후에도 다른 탭에서 읽은 상태를 즉시 동기화한다.
  refresh();
  window.setInterval(refresh, 10000);
  window.addEventListener("focus", refresh);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
})();

(() => {
  const inputs = document.querySelectorAll('input[type="file"][multiple][data-max-files]');
  for (const input of inputs) {
    input.addEventListener("change", () => {
      const maximum = Number(input.dataset.maxFiles) || 10;
      const error = document.getElementById(input.dataset.errorTarget || "");
      if (input.files.length > maximum) {
        input.value = "";
        input.setCustomValidity(`사진은 최대 ${maximum}장까지만 선택할 수 있습니다.`);
        if (error) error.hidden = false;
        input.reportValidity();
      } else {
        input.setCustomValidity("");
        if (error) error.hidden = true;
      }
    });
  }
})();
