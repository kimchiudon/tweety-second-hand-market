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
    const selection = [];
    const preview = document.createElement("div");
    preview.className = "upload-selection";
    preview.setAttribute("aria-live", "polite");
    input.insertAdjacentElement("afterend", preview);

    const keyFor = (file) => `${file.name}\u0000${file.size}\u0000${file.lastModified}\u0000${file.type}`;
    const formatSize = (bytes) => bytes < 1024 * 1024
      ? `${Math.max(1, Math.round(bytes / 1024))}KB`
      : `${(bytes / (1024 * 1024)).toFixed(1)}MB`;

    const syncInput = () => {
      if (typeof DataTransfer === "undefined") return false;
      const transfer = new DataTransfer();
      for (const file of selection) transfer.items.add(file);
      input.files = transfer.files;
      return true;
    };

    const render = () => {
      preview.replaceChildren();
      if (!selection.length) return;
      const summary = document.createElement("p");
      summary.className = "upload-selection-summary";
      summary.textContent = `선택한 사진 ${selection.length}장`;
      preview.append(summary);
      const list = document.createElement("ul");
      list.className = "upload-selection-list";
      selection.forEach((file, index) => {
        const item = document.createElement("li");
        const details = document.createElement("span");
        details.textContent = `${file.name} · ${formatSize(file.size)}`;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "upload-remove";
        remove.textContent = "삭제";
        remove.setAttribute("aria-label", `${file.name} 선택에서 삭제`);
        remove.addEventListener("click", () => {
          selection.splice(index, 1);
          syncInput();
          input.setCustomValidity("");
          const error = document.getElementById(input.dataset.errorTarget || "");
          if (error) error.hidden = true;
          render();
        });
        item.append(details, remove);
        list.append(item);
      });
      preview.append(list);
    };

    input.addEventListener("change", () => {
      const maximum = Number(input.dataset.maxFiles) || 10;
      const error = document.getElementById(input.dataset.errorTarget || "");
      if (typeof DataTransfer === "undefined") {
        if (input.files.length > maximum) {
          input.value = "";
          input.setCustomValidity(`사진은 최대 ${maximum}장까지만 선택할 수 있습니다.`);
          if (error) error.hidden = false;
          input.reportValidity();
        } else {
          input.setCustomValidity("");
          if (error) error.hidden = true;
        }
        return;
      }

      const existing = new Set(selection.map(keyFor));
      const additions = Array.from(input.files).filter((file) => !existing.has(keyFor(file)));
      if (selection.length + additions.length > maximum) {
        syncInput();
        input.setCustomValidity(`사진은 최대 ${maximum}장까지만 선택할 수 있습니다.`);
        if (error) error.hidden = false;
        input.reportValidity();
      } else {
        selection.push(...additions);
        syncInput();
        input.setCustomValidity("");
        if (error) error.hidden = true;
        render();
      }
    });

    input.form?.addEventListener("reset", () => {
      selection.splice(0);
      preview.replaceChildren();
    });
  }
})();
