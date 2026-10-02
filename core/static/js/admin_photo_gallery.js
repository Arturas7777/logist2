/* Галерея фото контейнера в списке админки. Тот же набор, что на портале:
   все / выгруженные / в контейнере, просмотр и скачивание выбранных. */
(function () {
  "use strict";

  var modal = document.getElementById("cm-photo-modal");
  if (!modal) return;

  var TYPE_LABEL = {
    IN_CONTAINER: "В контейнере",
    UNLOADING: "Выгруженные",
    GENERAL: "Прочие",
    LOADING: "Погрузка",
    SEAL: "Пломба",
  };

  var titleEl = document.getElementById("cm-photo-modal-title");
  var loadingEl = modal.querySelector(".cm-photo-modal__loading");
  var errorEl = modal.querySelector(".cm-photo-modal__error");
  var mainEl = modal.querySelector(".cm-photo-modal__main");
  var tabsEl = modal.querySelector(".cm-photo-modal__tabs");
  var gridEl = modal.querySelector(".cm-photo-modal__grid");
  var selectAllEl = document.getElementById("cm-photo-select-all");
  var downloadBtn = document.getElementById("cm-photo-download");
  var viewer = document.getElementById("cm-photo-viewer");
  var viewerImg = viewer.querySelector("[data-viewer-img]");
  var viewerStage = viewer.querySelector("[data-stage]");
  var viewerCounter = viewer.querySelector("[data-counter]");

  var photosUrl = modal.getAttribute("data-photos-url");
  var zipUrl = modal.getAttribute("data-zip-url");
  var currentId = null;
  var currentNumber = "";
  var groups = { all: [], unloading: [], inside: [] };
  var activeKey = "all";
  var viewerIndex = 0;
  var viewerScale = 1;
  var viewerPanX = 0;
  var viewerPanY = 0;
  var viewerDragging = false;
  var viewerDragX = 0;
  var viewerDragY = 0;

  function csrfToken() {
    var input = document.querySelector("[name=csrfmiddlewaretoken]");
    if (input && input.value) return input.value;
    var match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function urlFor(template, id) {
    return template.replace("/0/", "/" + id + "/");
  }

  function setBusy(state) {
    loadingEl.hidden = state !== "loading";
    errorEl.hidden = state !== "error";
    mainEl.hidden = state !== "ready";
  }

  function splitPhotos(photos) {
    var unloading = [];
    var inside = [];
    photos.forEach(function (photo) {
      if (photo.type === "IN_CONTAINER") inside.push(photo);
      else unloading.push(photo);
    });
    return { all: photos, unloading: unloading, inside: inside };
  }

  function activePhotos() {
    return groups[activeKey] || [];
  }

  function selectedIds() {
    return Array.prototype.map.call(
      gridEl.querySelectorAll(".cm-photo-card input:checked"),
      function (input) { return input.value; }
    );
  }

  function refreshDownload() {
    var count = selectedIds().length;
    downloadBtn.disabled = count === 0;
    downloadBtn.textContent = count ? "Скачать выбранные (" + count + ")" : "Скачать выбранные";
    var boxes = gridEl.querySelectorAll(".cm-photo-card input");
    var checked = gridEl.querySelectorAll(".cm-photo-card input:checked").length;
    selectAllEl.checked = boxes.length > 0 && checked === boxes.length;
    selectAllEl.indeterminate = checked > 0 && checked < boxes.length;
  }

  function renderGrid() {
    gridEl.textContent = "";
    activePhotos().forEach(function (photo, index) {
      var card = document.createElement("article");
      card.className = "cm-photo-card";

      var shot = document.createElement("button");
      shot.type = "button";
      shot.className = "cm-photo-card__shot";
      shot.title = "Открыть";
      var img = document.createElement("img");
      img.alt = TYPE_LABEL[photo.type] || "Фото";
      img.src = photo.thumbnail || photo.url;
      shot.appendChild(img);
      shot.addEventListener("click", function () { openViewer(index); });

      var meta = document.createElement("label");
      meta.className = "cm-photo-card__meta";
      var box = document.createElement("input");
      box.type = "checkbox";
      box.value = String(photo.id);
      var caption = document.createElement("span");
      caption.textContent = TYPE_LABEL[photo.type] || photo.type || "Фото";
      meta.appendChild(box);
      meta.appendChild(caption);

      card.appendChild(shot);
      card.appendChild(meta);
      gridEl.appendChild(card);
    });
    refreshDownload();
  }

  function renderTabs() {
    var defs = [
      ["all", "Все", groups.all.length],
      ["unloading", "Выгруженные", groups.unloading.length],
      ["inside", "В контейнере", groups.inside.length],
    ];
    tabsEl.textContent = "";
    defs.forEach(function (def) {
      if (!def[2]) return;
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = def[1] + " (" + def[2] + ")";
      if (def[0] === activeKey) button.className = "is-active";
      button.addEventListener("click", function () {
        activeKey = def[0];
        selectAllEl.checked = false;
        renderTabs();
        renderGrid();
      });
      tabsEl.appendChild(button);
    });
  }

  function showModal() {
    modal.hidden = false;
    document.body.style.overflow = "hidden";
  }

  function hideModal() {
    modal.hidden = true;
    hideViewer();
    document.body.style.overflow = "";
  }

  function openGallery(id, number) {
    currentId = id;
    currentNumber = number || "";
    titleEl.textContent = "Фотографии контейнера " + currentNumber;
    activeKey = "all";
    groups = { all: [], unloading: [], inside: [] };
    setBusy("loading");
    showModal();
    fetch(urlFor(photosUrl, id), { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error("load");
        return response.json();
      })
      .then(function (data) {
        if (!data.success || !data.photos || !data.photos.length) {
          errorEl.textContent = "Фотографии не найдены";
          setBusy("error");
          return;
        }
        groups = splitPhotos(data.photos);
        renderTabs();
        renderGrid();
        setBusy("ready");
      })
      .catch(function () {
        errorEl.textContent = "Не удалось загрузить фотографии";
        setBusy("error");
      });
  }

  function resetZoom() {
    viewerScale = 1;
    viewerPanX = 0;
    viewerPanY = 0;
  }

  function paintZoom(animated) {
    viewerImg.style.transition = animated ? "transform .2s ease" : "none";
    viewerImg.style.transform = "scale(" + viewerScale + ") translate(" + viewerPanX + "px, " + viewerPanY + "px)";
    viewerStage.style.cursor = viewerDragging ? "grabbing" : (viewerScale > 1 ? "grab" : "default");
    viewer.querySelectorAll(".cm-photo-viewer__nav").forEach(function (button) {
      button.classList.toggle("is-dim", viewerScale > 1);
    });
  }

  function applyViewer() {
    var photos = activePhotos();
    var photo = photos[viewerIndex];
    if (!photo) return;
    viewerImg.src = photo.url;
    paintZoom(false);
    viewerCounter.textContent = (viewerIndex + 1) + " / " + photos.length;
    viewer.querySelector("[data-nav='-1']").hidden = viewerIndex === 0;
    viewer.querySelector("[data-nav='1']").hidden = viewerIndex >= photos.length - 1;
  }

  function openViewer(index) {
    viewerIndex = index;
    resetZoom();
    applyViewer();
    viewer.hidden = false;
  }

  function hideViewer() {
    viewer.hidden = true;
    viewerImg.removeAttribute("src");
  }

  function stepViewer(delta) {
    var photos = activePhotos();
    var next = viewerIndex + delta;
    if (next < 0 || next >= photos.length) return;
    viewerIndex = next;
    resetZoom();
    applyViewer();
  }

  selectAllEl.addEventListener("change", function () {
    gridEl.querySelectorAll(".cm-photo-card input").forEach(function (input) {
      input.checked = selectAllEl.checked;
    });
    refreshDownload();
  });
  gridEl.addEventListener("change", function (event) {
    if (event.target.matches && event.target.matches("input")) refreshDownload();
  });

  downloadBtn.addEventListener("click", function () {
    var ids = selectedIds();
    if (!ids.length || !currentId) return;
    downloadBtn.disabled = true;
    fetch(urlFor(zipUrl, currentId), {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      body: JSON.stringify({ photo_ids: ids }),
    })
      .then(function (response) {
        if (!response.ok) throw new Error("zip");
        return response.blob();
      })
      .then(function (blob) {
        var link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = "container_photos_" + (currentNumber || currentId) + ".zip";
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(function () { URL.revokeObjectURL(link.href); }, 1500);
      })
      .catch(function () {
        window.alert("Не удалось скачать архив");
      })
      .finally(refreshDownload);
  });

  modal.addEventListener("click", function (event) {
    if (event.target === modal || event.target.closest("[data-close]")) hideModal();
  });

  viewer.querySelector("[data-viewer-close]").addEventListener("click", hideViewer);
  viewer.querySelectorAll("[data-nav]").forEach(function (button) {
    button.addEventListener("click", function () {
      stepViewer(Number(button.getAttribute("data-nav")));
    });
  });
  viewer.querySelector("[data-download]").addEventListener("click", function () {
    var photo = activePhotos()[viewerIndex];
    if (!photo) return;
    var link = document.createElement("a");
    link.href = photo.url;
    link.download = "";
    link.target = "_blank";
    link.rel = "noopener";
    link.click();
  });
  viewer.querySelectorAll("[data-zoom]").forEach(function (button) {
    button.addEventListener("click", function (event) {
      event.stopPropagation();
      var mode = button.getAttribute("data-zoom");
      if (mode === "in") viewerScale = Math.min(viewerScale + 0.5, 5);
      else if (mode === "out") viewerScale = Math.max(viewerScale - 0.5, 1);
      else viewerScale = 1;
      if (viewerScale === 1) {
        viewerPanX = 0;
        viewerPanY = 0;
      }
      paintZoom(true);
    });
  });

  viewerStage.addEventListener("wheel", function (event) {
    if (viewer.hidden) return;
    event.preventDefault();
    viewerScale = Math.min(Math.max(viewerScale + (event.deltaY > 0 ? -0.1 : 0.1), 1), 5);
    if (viewerScale === 1) {
      viewerPanX = 0;
      viewerPanY = 0;
    }
    paintZoom(true);
  }, { passive: false });

  viewerStage.addEventListener("mousedown", function (event) {
    if (viewerScale <= 1 || event.button !== 0) return;
    event.preventDefault();
    viewerDragging = true;
    viewerDragX = event.clientX - viewerPanX;
    viewerDragY = event.clientY - viewerPanY;
    paintZoom(false);
  });
  document.addEventListener("mousemove", function (event) {
    if (!viewerDragging) return;
    viewerPanX = event.clientX - viewerDragX;
    viewerPanY = event.clientY - viewerDragY;
    paintZoom(false);
  });
  document.addEventListener("mouseup", function () {
    if (!viewerDragging) return;
    viewerDragging = false;
    paintZoom(false);
  });
  viewer.addEventListener("click", function (event) {
    if (event.target === viewer) hideViewer();
  });

  document.addEventListener("click", function (event) {
    var button = event.target.closest(".cm-photo-open");
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    openGallery(button.getAttribute("data-container-id"), button.getAttribute("data-container-number"));
  });

  document.addEventListener("keydown", function (event) {
    if (modal.hidden) return;
    if (!viewer.hidden) {
      if (event.key === "Escape") {
        event.preventDefault();
        hideViewer();
      } else if (event.key === "ArrowLeft") {
        stepViewer(-1);
      } else if (event.key === "ArrowRight") {
        stepViewer(1);
      }
      return;
    }
    if (event.key === "Escape") hideModal();
  });
})();
