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
                    
                    const lamp = card.querySelector('.status-lamp');
                    const statusText = card.querySelector('.status-text');
                    const bitrateText = card.querySelector('.camera-bitrate-value');
                    const recBtn = card.querySelector(`#btn_rec_${cameraId}`);
                    const recStatus = card.querySelector(`#rec_status_wrapper_${cameraId}`);

                    if (cameraData) {
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

// Запускаем сразу при загрузке
updateServerStatus();
// И повторяем каждые 30 секунд
setInterval(updateServerStatus, 30000);