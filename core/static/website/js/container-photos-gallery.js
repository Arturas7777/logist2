/**
 * Галерея фотографий контейнера (модалка на главной и в кабинете).
 * Открытие: ContainerPhotosGallery.open(number) или клик по [data-container-photos].
 */
(function (global) {
    'use strict';

    let photosModal = null;
    let currentContainerNumber = null;
    let currentContainerToken = null;
    let initialized = false;
    let currentPhotoIndex = 0;
    let currentTabPhotosUrls = [];
    let _viewerKeyHandler = null;
    let _viewerScale = 1;

    function getCsrfToken() {
        const el = document.querySelector('[name=csrfmiddlewaretoken]');
        if (el && el.value) return el.value;
        const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function init() {
        if (initialized) return;
        const el = document.getElementById('photosModal');
        if (!el || typeof bootstrap === 'undefined') return;
        photosModal = new bootstrap.Modal(el);
        el.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && document.getElementById('photo-viewer')) {
                e.stopImmediatePropagation();
                e.preventDefault();
            }
        }, true);
        document.addEventListener('click', function (e) {
            const trigger = e.target.closest('[data-container-photos]');
            if (!trigger) return;
            e.preventDefault();
            open(trigger.getAttribute('data-container-photos'));
        });
        initialized = true;
    }

    function open(containerNumber) {
        if (!containerNumber) return;
        if (!initialized) init();
        if (!photosModal) return;
        currentContainerNumber = containerNumber;
        loadContainerPhotos(containerNumber);
        photosModal.show();
    }

    function loadContainerPhotos(containerNumber) {
        const photosLoading = document.getElementById('photos-loading');
        const photosContent = document.getElementById('photos-content');
        const photosError = document.getElementById('photos-error');

        const photosGridAll = document.getElementById('photos-grid-all');
        const photosGridInContainer = document.getElementById('photos-grid-in-container');
        const photosGridUnloading = document.getElementById('photos-grid-unloading');

        const inContainerTabLi = document.getElementById('in-container-tab-li');
        const unloadingTabLi = document.getElementById('unloading-tab-li');

        photosLoading.style.display = 'block';
        photosContent.style.display = 'none';
        photosError.style.display = 'none';

        fetch('/api/container-photos/' + encodeURIComponent(containerNumber) + '/')
            .then(function (response) { return response.json(); })
            .then(function (data) {
                photosLoading.style.display = 'none';

                if (data.success && data.photos.length > 0) {
                    currentContainerToken = data.container_token || null;

                    photosGridAll.innerHTML = '';
                    photosGridInContainer.innerHTML = '';
                    photosGridUnloading.innerHTML = '';

                    let inContainerCount = 0;
                    let unloadingCount = 0;

                    data.photos.forEach(function (photo) {
                        const photoCard = createPhotoCard(photo);
                        photosGridAll.appendChild(photoCard.cloneNode(true));

                        if (photo.photo_type_code === 'IN_CONTAINER') {
                            photosGridInContainer.appendChild(photoCard.cloneNode(true));
                            inContainerCount++;
                        } else if (photo.photo_type_code === 'UNLOADING') {
                            photosGridUnloading.appendChild(photoCard.cloneNode(true));
                            unloadingCount++;
                        } else {
                            photosGridUnloading.appendChild(photoCard.cloneNode(true));
                            unloadingCount++;
                        }
                    });

                    inContainerTabLi.style.display = inContainerCount > 0 ? 'block' : 'none';
                    unloadingTabLi.style.display = unloadingCount > 0 ? 'block' : 'none';

                    document.getElementById('all-photos-tab').click();

                    photosContent.style.display = 'block';
                    setupPhotoSelection();
                    setupPhotoZoom();
                } else {
                    photosError.innerHTML = '<i class="bi bi-exclamation-triangle"></i> Фотографии не найдены';
                    photosError.style.display = 'block';
                }
            })
            .catch(function (error) {
                console.error('Error:', error);
                photosLoading.style.display = 'none';
                photosError.style.display = 'block';
            });
    }

    function createPhotoCard(photo) {
        const photoCard = document.createElement('div');
        photoCard.className = 'col-6 col-md-4 col-lg-3';
        photoCard.innerHTML =
            '<div class="card h-100">' +
            '<div class="card-img-top-container" style="aspect-ratio: 4/3; overflow: hidden; cursor: pointer; position: relative;">' +
            '<img src="' + photo.thumbnail_url + '" class="card-img-top photo-preview" style="width: 100%; height: 100%; object-fit: cover;" alt="' + (photo.description || '') + '" data-full-url="' + photo.url + '" loading="lazy">' +
            '<div class="photo-overlay" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0); transition: all 0.3s; display: flex; align-items: center; justify-content: center;">' +
            '<i class="bi bi-zoom-in" style="color: white; font-size: 2rem; opacity: 0; transition: opacity 0.3s;"></i>' +
            '</div></div>' +
            '<div class="card-body p-2"><div class="form-check">' +
            '<input class="form-check-input photo-checkbox" type="checkbox" value="' + photo.id + '" id="photo_' + photo.id + '_' + Math.random().toString(36).substr(2, 9) + '">' +
            '<label class="form-check-label"><small class="text-muted">' + photo.photo_type + '</small></label>' +
            '</div></div></div>';
        return photoCard;
    }

    function setupPhotoSelection() {
        const selectAllCheckbox = document.getElementById('selectAllPhotos');
        const downloadBtn = document.getElementById('downloadSelectedBtn');
        const photosContent = document.getElementById('photos-content');

        const newSelectAll = selectAllCheckbox.cloneNode(true);
        selectAllCheckbox.parentNode.replaceChild(newSelectAll, selectAllCheckbox);

        const newDownloadBtn = downloadBtn.cloneNode(true);
        downloadBtn.parentNode.replaceChild(newDownloadBtn, downloadBtn);

        function getActiveTabCheckboxes() {
            const activePane = document.querySelector('#photosTabContent .tab-pane.active');
            return activePane ? Array.from(activePane.querySelectorAll('.photo-checkbox')) : [];
        }

        function getSelectedPhotoIds() {
            const checkboxes = getActiveTabCheckboxes();
            const ids = new Set();
            checkboxes.forEach(function (cb) {
                if (cb.checked) ids.add(cb.value);
            });
            return Array.from(ids);
        }

        function updateDownloadButton() {
            const count = getSelectedPhotoIds().length;
            newDownloadBtn.disabled = count === 0;
            newDownloadBtn.innerHTML = '<i class="bi bi-download"></i> Скачать выбранные' + (count > 0 ? ' (' + count + ')' : '');
        }

        newSelectAll.addEventListener('change', function () {
            const checkboxes = getActiveTabCheckboxes();
            checkboxes.forEach(function (checkbox) {
                checkbox.checked = this.checked;
            }, this);
            updateDownloadButton();
        });

        photosContent.addEventListener('change', function (e) {
            if (e.target.classList.contains('photo-checkbox')) {
                updateDownloadButton();
                const checkboxes = getActiveTabCheckboxes();
                const checkedCount = checkboxes.filter(function (cb) { return cb.checked; }).length;
                const totalCount = checkboxes.length;
                newSelectAll.checked = checkedCount === totalCount && totalCount > 0;
                newSelectAll.indeterminate = checkedCount > 0 && checkedCount < totalCount;
            }
        });

        document.querySelectorAll('#photosTabs button').forEach(function (tab) {
            tab.addEventListener('shown.bs.tab', function () {
                newSelectAll.checked = false;
                newSelectAll.indeterminate = false;
                updateDownloadButton();
            });
        });

        newDownloadBtn.addEventListener('click', function () {
            const photoIds = getSelectedPhotoIds();
            if (photoIds.length > 0) {
                downloadPhotosArchive(photoIds);
            }
        });

        updateDownloadButton();
    }

    function downloadPhotosArchive(photoIds) {
        fetch('/api/download-photos-archive/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken()
            },
            body: JSON.stringify({
                photo_ids: photoIds,
                container_token: currentContainerToken || ''
            })
        })
            .then(function (response) {
                if (response.ok) return response.blob();
                throw new Error('Ошибка при скачивании архива');
            })
            .then(function (blob) {
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'container_photos_' + currentContainerNumber + '.zip';
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);
            })
            .catch(function (error) {
                console.error('Error:', error);
                alert('Ошибка при скачивании архива');
            });
    }

    function setupPhotoZoom() {
        function getActiveTabPhotos() {
            const activePane = document.querySelector('#photosTabContent .tab-pane.active');
            if (!activePane) return [];
            const images = activePane.querySelectorAll('.photo-preview');
            return Array.from(images).map(function (img) { return img.getAttribute('data-full-url'); });
        }

        function getPhotoIndexInActiveTab(container) {
            const activePane = document.querySelector('#photosTabContent .tab-pane.active');
            if (!activePane) return 0;
            const containers = Array.from(activePane.querySelectorAll('.card-img-top-container'));
            return containers.indexOf(container);
        }

        const allContainers = document.querySelectorAll('#photosModal .card-img-top-container');

        allContainers.forEach(function (container) {
            const overlay = container.querySelector('.photo-overlay');
            const icon = overlay.querySelector('i');

            container.addEventListener('mouseenter', function () {
                overlay.style.background = 'rgba(0,0,0,0.5)';
                icon.style.opacity = '1';
            });

            container.addEventListener('mouseleave', function () {
                overlay.style.background = 'rgba(0,0,0,0)';
                icon.style.opacity = '0';
            });

            container.addEventListener('click', function () {
                currentTabPhotosUrls = getActiveTabPhotos();
                currentPhotoIndex = getPhotoIndexInActiveTab(container);
                if (currentPhotoIndex >= 0 && currentTabPhotosUrls.length > 0) {
                    openPhotoViewer(currentTabPhotosUrls[currentPhotoIndex]);
                }
            });
        });
    }

    function openPhotoViewer(imageUrl) {
        const viewer = document.createElement('div');
        viewer.id = 'photo-viewer';
        viewer.style.cssText = 'position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 0, 0, 0.95); z-index: 9999; display: flex; align-items: center; justify-content: center;';

        viewer.innerHTML =
            '<div style="position: relative; width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;">' +
            '<div id="image-container" style="width: 90%; height: 90%; display: flex; align-items: center; justify-content: center; overflow: hidden; cursor: grab;">' +
            '<img id="viewer-image" src="' + imageUrl + '" style="max-width: 100%; max-height: 100%; object-fit: contain; border-radius: 8px;">' +
            '</div>' +
            '<button id="prev-photo" class="nav-btn" style="position: absolute; left: 10px; top: 50%; transform: translateY(-50%); background: rgba(255,255,255,0.35); border: none; color: rgba(0,0,0,0.7); padding: 12px 16px; border-radius: 50%; cursor: pointer; font-size: 1.2rem; z-index: 10000; transition: opacity 0.3s ease, background 0.2s ease; ' + (currentPhotoIndex > 0 ? '' : 'display:none;') + '">' +
            '<i class="bi bi-chevron-left"></i></button>' +
            '<button id="next-photo" class="nav-btn" style="position: absolute; right: 10px; top: 50%; transform: translateY(-50%); background: rgba(255,255,255,0.35); border: none; color: rgba(0,0,0,0.7); padding: 12px 16px; border-radius: 50%; cursor: pointer; font-size: 1.2rem; z-index: 10000; transition: opacity 0.3s ease, background 0.2s ease; ' + (currentPhotoIndex < currentTabPhotosUrls.length - 1 ? '' : 'display:none;') + '">' +
            '<i class="bi bi-chevron-right"></i></button>' +
            '<div style="position: absolute; top: 20px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.7); padding: 10px 20px; border-radius: 25px; color: white; z-index: 10000;">' +
            '<span data-photo-counter>' + (currentPhotoIndex + 1) + ' / ' + currentTabPhotosUrls.length + '</span></div>' +
            '<div style="position: absolute; top: 20px; right: 20px; display: flex; gap: 10px; z-index: 10000;">' +
            '<button id="zoom-in" style="background: rgba(255,255,255,0.9); border: none; color: black; padding: 10px 15px; border-radius: 5px; cursor: pointer;"><i class="bi bi-zoom-in"></i></button>' +
            '<button id="zoom-out" style="background: rgba(255,255,255,0.9); border: none; color: black; padding: 10px 15px; border-radius: 5px; cursor: pointer;"><i class="bi bi-zoom-out"></i></button>' +
            '<button id="zoom-reset" style="background: rgba(255,255,255,0.9); border: none; color: black; padding: 10px 15px; border-radius: 5px; cursor: pointer;"><i class="bi bi-arrows-angle-contract"></i></button>' +
            '<button id="download-single" style="background: var(--accent-green); border: none; color: white; padding: 10px 15px; border-radius: 5px; cursor: pointer;"><i class="bi bi-download"></i></button>' +
            '<button id="close-viewer" onclick="event.stopPropagation(); window._closePhotoViewer && window._closePhotoViewer();" style="background: var(--accent-red); border: none; color: white; padding: 10px 15px; border-radius: 5px; cursor: pointer;"><i class="bi bi-x-lg"></i></button>' +
            '</div></div>';

        document.body.appendChild(viewer);
        document.body.style.overflow = 'hidden';

        let scale = 1;
        let translateX = 0;
        let translateY = 0;
        let isDragging = false;
        let startX, startY;

        const img = document.getElementById('viewer-image');
        const container = document.getElementById('image-container');

        function updateTransform(withTransition) {
            img.style.transition = withTransition ? 'transform 0.3s ease' : 'none';
            img.style.transform = 'scale(' + scale + ') translate(' + translateX + 'px, ' + translateY + 'px)';
            container.style.cursor = scale > 1 ? 'grab' : 'default';
            document.querySelectorAll('.nav-btn').forEach(function (btn) {
                btn.style.opacity = scale > 1 ? '0' : '1';
                btn.style.pointerEvents = scale > 1 ? 'none' : 'auto';
            });
        }

        document.getElementById('zoom-in')?.addEventListener('click', function (e) {
            e.stopPropagation();
            scale = Math.min(scale + 0.5, 5);
            updateTransform(true);
        });

        document.getElementById('zoom-out')?.addEventListener('click', function (e) {
            e.stopPropagation();
            scale = Math.max(scale - 0.5, 1);
            if (scale === 1) {
                translateX = 0;
                translateY = 0;
            }
            updateTransform(true);
        });

        document.getElementById('zoom-reset')?.addEventListener('click', function (e) {
            e.stopPropagation();
            scale = 1;
            translateX = 0;
            translateY = 0;
            updateTransform(true);
        });

        container.addEventListener('wheel', function (e) {
            e.preventDefault();
            const delta = e.deltaY > 0 ? -0.1 : 0.1;
            scale = Math.min(Math.max(scale + delta, 1), 5);
            if (scale === 1) {
                translateX = 0;
                translateY = 0;
            }
            updateTransform(true);
        });

        container.addEventListener('mousedown', function (e) {
            if (scale > 1) {
                e.preventDefault();
                isDragging = true;
                container.style.cursor = 'grabbing';
                startX = e.clientX - translateX;
                startY = e.clientY - translateY;
            }
        });

        function handleMouseMove(e) {
            if (isDragging) {
                e.preventDefault();
                translateX = e.clientX - startX;
                translateY = e.clientY - startY;
                updateTransform(false);
            }
        }

        function handleMouseUp() {
            if (isDragging) {
                isDragging = false;
                if (scale > 1) container.style.cursor = 'grab';
            }
        }

        document.addEventListener('mousemove', handleMouseMove);
        document.addEventListener('mouseup', handleMouseUp);
        viewer._dragHandlers = { move: handleMouseMove, up: handleMouseUp };

        let touchStartX = 0;
        let touchStartY = 0;
        let touchStartDist = 0;
        let initialScale = 1;
        let isSwiping = false;

        function getTouchDistance(touches) {
            const dx = touches[0].clientX - touches[1].clientX;
            const dy = touches[0].clientY - touches[1].clientY;
            return Math.sqrt(dx * dx + dy * dy);
        }

        container.addEventListener('touchstart', function (e) {
            if (e.touches.length === 2) {
                touchStartDist = getTouchDistance(e.touches);
                initialScale = scale;
            } else if (e.touches.length === 1 && scale === 1) {
                touchStartX = e.touches[0].clientX;
                touchStartY = e.touches[0].clientY;
                isSwiping = true;
            }
        }, { passive: true });

        container.addEventListener('touchmove', function (e) {
            if (e.touches.length === 2) {
                e.preventDefault();
                const currentDist = getTouchDistance(e.touches);
                const ratio = currentDist / touchStartDist;
                scale = Math.min(Math.max(initialScale * ratio, 1), 5);
                if (scale === 1) {
                    translateX = 0;
                    translateY = 0;
                }
                updateTransform(false);
            } else if (e.touches.length === 1 && scale > 1) {
                e.preventDefault();
                if (isDragging) {
                    translateX = e.touches[0].clientX - startX;
                    translateY = e.touches[0].clientY - startY;
                    updateTransform(false);
                }
            }
        }, { passive: false });

        container.addEventListener('touchend', function (e) {
            if (isSwiping && scale === 1 && e.changedTouches.length === 1) {
                const touchEndX = e.changedTouches[0].clientX;
                const touchEndY = e.changedTouches[0].clientY;
                const diffX = touchEndX - touchStartX;
                const diffY = touchEndY - touchStartY;
                if (Math.abs(diffX) > Math.abs(diffY) && Math.abs(diffX) > 50) {
                    if (diffX > 0 && currentPhotoIndex > 0) {
                        currentPhotoIndex--;
                        updateViewerImage();
                    } else if (diffX < 0 && currentPhotoIndex < currentTabPhotosUrls.length - 1) {
                        currentPhotoIndex++;
                        updateViewerImage();
                    }
                }
            }
            isSwiping = false;
            touchStartDist = 0;
        }, { passive: true });

        container.addEventListener('touchstart', function (e) {
            if (e.touches.length === 1 && scale > 1) {
                isDragging = true;
                startX = e.touches[0].clientX - translateX;
                startY = e.touches[0].clientY - translateY;
            }
        }, { passive: true });

        container.addEventListener('touchend', function () {
            isDragging = false;
        }, { passive: true });

        document.getElementById('prev-photo')?.addEventListener('click', function (e) {
            e.stopPropagation();
            if (currentPhotoIndex > 0) {
                currentPhotoIndex--;
                updateViewerImage();
            }
        });

        document.getElementById('next-photo')?.addEventListener('click', function (e) {
            e.stopPropagation();
            if (currentPhotoIndex < currentTabPhotosUrls.length - 1) {
                currentPhotoIndex++;
                updateViewerImage();
            }
        });

        function updateViewerImage() {
            const viewerImg = document.getElementById('viewer-image');
            if (!viewerImg) return;
            scale = 1;
            _viewerScale = 1;
            translateX = 0;
            translateY = 0;
            viewerImg.src = currentTabPhotosUrls[currentPhotoIndex];
            updateTransform(true);

            const counter = viewer.querySelector('[data-photo-counter]');
            if (counter) counter.textContent = (currentPhotoIndex + 1) + ' / ' + currentTabPhotosUrls.length;

            const prevBtn = document.getElementById('prev-photo');
            const nextBtn = document.getElementById('next-photo');
            if (prevBtn) prevBtn.style.display = currentPhotoIndex > 0 ? '' : 'none';
            if (nextBtn) nextBtn.style.display = currentPhotoIndex < currentTabPhotosUrls.length - 1 ? '' : 'none';
        }

        const origUpdateTransform = updateTransform;
        updateTransform = function (wt) {
            origUpdateTransform(wt);
            _viewerScale = scale;
        };

        let clickStartX = 0, clickStartY = 0;
        viewer.addEventListener('mousedown', function (e) {
            clickStartX = e.clientX;
            clickStartY = e.clientY;
        });

        viewer.addEventListener('click', function (e) {
            if (Math.abs(e.clientX - clickStartX) > 5 || Math.abs(e.clientY - clickStartY) > 5) return;
            if (e.target.tagName === 'IMG') return;
            closePhotoViewer();
        });

        document.getElementById('download-single').addEventListener('click', function (e) {
            e.stopPropagation();
            const link = document.createElement('a');
            link.href = currentTabPhotosUrls[currentPhotoIndex];
            link.download = currentTabPhotosUrls[currentPhotoIndex].split('/').pop();
            link.click();
        });

        if (_viewerKeyHandler) {
            document.removeEventListener('keydown', _viewerKeyHandler, true);
        }
        _viewerKeyHandler = function (e) {
            if (!document.getElementById('photo-viewer')) return;
            if (e.key === 'Escape') {
                e.preventDefault();
                e.stopImmediatePropagation();
                closePhotoViewer();
            } else if (e.key === 'ArrowLeft') {
                e.preventDefault();
                if (currentPhotoIndex > 0) {
                    currentPhotoIndex--;
                    updateViewerImage();
                }
            } else if (e.key === 'ArrowRight') {
                e.preventDefault();
                if (currentPhotoIndex < currentTabPhotosUrls.length - 1) {
                    currentPhotoIndex++;
                    updateViewerImage();
                }
            }
        };
        document.addEventListener('keydown', _viewerKeyHandler, true);
    }

    function closePhotoViewer() {
        const viewer = document.getElementById('photo-viewer');
        if (viewer) {
            if (viewer._dragHandlers) {
                document.removeEventListener('mousemove', viewer._dragHandlers.move);
                document.removeEventListener('mouseup', viewer._dragHandlers.up);
            }
            if (_viewerKeyHandler) {
                document.removeEventListener('keydown', _viewerKeyHandler, true);
                _viewerKeyHandler = null;
            }
            viewer.remove();
            document.body.style.overflow = '';
        }
    }

    global._closePhotoViewer = closePhotoViewer;
    global.ContainerPhotosGallery = { init: init, open: open };

    document.addEventListener('DOMContentLoaded', init);
})(window);
