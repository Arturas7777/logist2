/* Копирование VIN и номера контейнера по клику на .vin-copy-btn. */
(function () {
  "use strict";

  function copyFrom(btn) {
    var value = btn.getAttribute("data-vin");
    if (!value || !navigator.clipboard) return;
    var title = btn.getAttribute("data-copy-title") || btn.getAttribute("title") || "Копировать";
    btn.setAttribute("data-copy-title", title);
    navigator.clipboard.writeText(value).then(function () {
      btn.classList.add("copied");
      btn.setAttribute("title", "Скопировано");
      window.setTimeout(function () {
        btn.classList.remove("copied");
        btn.setAttribute("title", title);
      }, 1400);
    });
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".vin-copy-btn");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    copyFrom(btn);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var btn = e.target.closest && e.target.closest(".vin-copy-btn");
    if (!btn) return;
    e.preventDefault();
    copyFrom(btn);
  });
})();
