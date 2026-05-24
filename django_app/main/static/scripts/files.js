document.getElementById('searchInput').addEventListener('input', function () {
    const filter = this.value.toLowerCase(); // Приводим запрос к нижнему регистру
    const rows = document.querySelectorAll('#subscriberTable1 tbody tr');

    rows.forEach(row => {
        // Получаем текстовое содержимое всей строки
        const text = row.textContent.toLowerCase();
        
        // Если запрос найден в тексте строки, показываем её, иначе — скрываем
        if (text.includes(filter)) {
            row.style.display = '';
        } else {
            row.style.display = 'none';
        }
    });
});