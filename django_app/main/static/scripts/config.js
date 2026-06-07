document.addEventListener("DOMContentLoaded", function() {
    
function updateAllCamerasDashboard() {
    fetch('/api/get_all_cameras_status/')
        .then(response => response.json())
        .then(response_data => {
            if (response_data.status === 'success') {
                const data = response_data.data;

                // Проходимся по каждой карточке на странице
                document.querySelectorAll('.camera-monitor-card').forEach(card => {
                    const cameraId = card.getAttribute('data-camera-id');
                    const cameraData = data[cameraId];
                    const resSelect = card.querySelector('.camera-res-select');
                    
                    const lamp = card.querySelector('.status-lamp');
                    const statusText = card.querySelector('.status-text');

                    const bitrateText = card.querySelector('.camera-bitrate-value');
                    const recBtn = card.querySelector(`#btn_rec_${cameraId}`);
                    const recStatus = card.querySelector(`#rec_status_wrapper_${cameraId}`);
                    const resText = card.querySelector('.camera-onvif-res');
                    const fpsText = card.querySelector('.camera-onvif-fps');
                    const onvifInfoBlock = card.querySelector('.camera-onvif-info');
                    const fpsSelect = card.querySelector('.camera-fps-select');

                    if (cameraData) {
                        const supported = cameraData.onvif_info ? cameraData.onvif_info.supported : null;

                        if (cameraData.onvif_info && cameraData.onvif_info.status === 'online') {
                            if (onvifInfoBlock) onvifInfoBlock.style.display = 'block';
                            resText.textContent = cameraData.onvif_info.res;
                            fpsText.textContent = cameraData.onvif_info.fps;
                            if (resSelect.options.length <= 1 && supported.resolutions.length > 0) {
                                supported.resolutions.forEach(res => {
                                    let option = document.createElement('option');
                                    option.value = res;
                                    option.text = res;
                                    resSelect.add(option);
                                });
                            }
                            
                            if (fpsSelect && supported && supported.fps_range && fpsSelect.options.length <= 1) {
                                const { min, max } = supported.fps_range;
                                // Генерируем варианты от min до max (с шагом 5 для удобства)
                                for (let i = min; i <= max; i += 5) {
                                    let option = document.createElement('option');
                                    option.value = i;
                                    option.text = i + " FPS";
                                    fpsSelect.add(option);
                                }
                            }
                        } else {
                            resText.textContent = "N/A";
                            fpsText.textContent = "N/A";
                            // Если камера не поддерживает ONVIF, скрываем блок с разрешением и FPS
                            
                            if (onvifInfoBlock) onvifInfoBlock.style.display = 'none';
                        }


                        if (cameraData.status === 'online') {
                            // Камера транслирует поток
                            lamp.className = 'status-lamp green';
                            statusText.textContent = 'В сети';
                            statusText.className = 'status-text text-success';
                            bitrateText.textContent = `${cameraData.bitrate_mbps} Mbps`;
                            bitrateText.className = 'camera-bitrate-value text-success';
                              // ВКЛЮЧАЕМ кнопку, если камера в сети
                            if (recBtn) recBtn.disabled = false;

                        } else {
                            // Камера отвалилась
                            lamp.className = 'status-lamp red';
                            statusText.textContent = 'Оффлайн';
                            statusText.className = 'status-text text-danger';
                            bitrateText.textContent = '0.00 Mbps';
                            bitrateText.className = 'camera-bitrate-value text-muted';
                            recStatus.textContent = ''

                            // БЛОКИРУЕМ кнопку, если камера оффлайн
                            if (recBtn) {
                                recBtn.disabled = true;
                                // Опционально: меняем текст кнопки при блоке
                                recBtn.textContent = 'Начать запись';
                            }
                        }
                    }
                });
            }
        })
        .catch(err => {
            console.error("Ошибка мониторинга камер:", err);
            // В случае падения сети красим индикаторы обратно в серый статус
            document.querySelectorAll('.camera-monitor-card').forEach(card => {
                card.querySelector('.status-lamp').className = 'status-lamp gray';
                card.querySelector('.status-text').textContent = 'Сбой опроса';
                card.querySelector('.status-text').className = 'status-text text-muted';
            });
        });
}

    // Запускаем мониторинг: первый раз сразу, затем каждые 2 секунды
    updateAllCamerasDashboard();
    setInterval(updateAllCamerasDashboard, 2000);
});

function updateServerStatus() {
    const lamp = document.getElementById('server-status-lamp');
    const text = document.getElementById('server-status-text');

    fetch('/api/ping-server/')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'online') {
                lamp.style.backgroundColor = '#28a745'; // Зеленый
                lamp.style.boxShadow = '0 0 8px #28a745';
                text.textContent = 'MediaMTX: Online';
            } else {
                lamp.style.backgroundColor = '#dc3545'; // Красный
                lamp.style.boxShadow = '0 0 8px #dc3545';
                text.textContent = 'MediaMTX: Offline';
            }
        })
        .catch(error => {
            lamp.style.backgroundColor = '#6c757d'; // Серый
            text.textContent = 'Ошибка связи';
        });
}


function changeCameraRes(selectElement) {
    const cameraId = selectElement.getAttribute('data-camera-id');
    const newRes = selectElement.value;
    
    fetch(`/api/set_camera_res/${cameraId}/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value
        },
        body: JSON.stringify({ resolution: newRes })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            alert('Настройки применены!');
        } else {
            alert('Ошибка: ' + data.message);
        }
    });
}


function applyCameraSettings(element) {
    const card = element.closest('.camera-monitor-card');
    const cameraId = element.getAttribute('data-camera-id');
    
    const fps = card.querySelector('.camera-fps-select').value;
    
    const payload = {};
    if (fps) payload.fps = fps;

    fetch(`/api/set_camera_fps/${cameraId}/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value
        },
        body: JSON.stringify(payload)
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            alert('Настройки применены!');
        } else {
            alert('Ошибка: ' + data.message);
        }
    });
}



// Запускаем сразу при загрузке
updateServerStatus();
// И повторяем каждые 30 секунд
setInterval(updateServerStatus, 30000);