async function PlayFunc(id, name) {
    const message = document.getElementById('play_message');
    const myModal = document.getElementById('Play');
    const card = document.getElementById('card_' + id);
    const playButton = document.getElementById('play_button');

    if (!card || !myModal) return;

    // Стилизация активной карточки
    card.style.boxShadow = '2px 2px 4px rgba(10, 10, 10, 0.5)';
    card.style.transform = 'translate(-5px, -5px)';
    message.textContent = `Вы действительно хотите запустить трансляцию ${name}?`;

    // Очищаем старые обработчики и ставим новый на кнопку "Запустить"
    playButton.onclick = async function() {
        try {
            // Запрос к созданному View
            const response = await fetch(`/get-stream/${id}`);
            
            if (!response.ok) throw new Error('Ошибка при получении данных');

            const data = await response.json();

            if (data.url) {
                console.log("Stream URL:", data.url);
                // Открывает ссылку в новой вкладке
                window.open(data.url, '_blank');
            } else {
                alert('Не удалось сформировать ссылку на поток');
            }
        } catch (error) {
            console.error('Fetch error:', error);
            alert('Произошла ошибка при запуске');
        }
    };

    // Возвращаем стили карточки на место при закрытии окна
    myModal.addEventListener('hide.bs.modal', () => {
        card.style.boxShadow = '';
        card.style.transform = '';
    }, { once: true });
}

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