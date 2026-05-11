function EditFunc(element) {
    // 1. Извлекаем данные из атрибутов
    const cameraData = {
        id: element.dataset.id,
        name: element.dataset.name,
        description: element.dataset.description,
        address: element.dataset.address,
        login: element.dataset.login,
        password:element.dataset.password,
        port:element.dataset.port,
    }


// 2. Находим поля в модальном окне редактирования
    const nameInput = document.getElementById('edit_camera_name_input');
    const descInput = document.getElementById('edit_description_input');
    const addrInput = document.getElementById('edit_camera_address_input');
    const loginInput = document.getElementById('edit_camera_login_input');
    const portInput = document.getElementById('edit_camera_port_input');
    const passInput = document.getElementById('edit_camera_code_input'); // Поле пароля в модалке ред.
    const pathInput = document.getElementById('edit_camera_path_input');


    // 3. Заполняем поля значениями (с проверкой на существование)
    if (nameInput) nameInput.value = cameraData.name || '';
    if (descInput) descInput.value = cameraData.description || '';
    if (addrInput) addrInput.value = cameraData.address || '';
    if (loginInput) loginInput.value = cameraData.login || '';
    if (portInput) portInput.value = cameraData.port || '';
    if (passInput) passInput.value = cameraData.password || '';
    if (pathInput) pathInput.value = cameraData.path || '/stream1';

    // 4. Если нужно менять URL формы для удаления или сохранения:
    const edit_form = document.getElementById('edit_camera_form');
    const delete_form = document.getElementById('delete_camera_form');
    edit_form.action = 'edit_camera/' + cameraData.id;
    delete_form.action = 'delete_camera/' + cameraData.id;

}


function togglePass() {
    // Находим чекбокс, который вызвал функцию
    const checkbox = event.target;
    // Находим родительский контейнер (div class="mb-3")
    const container = checkbox.closest('.mb-3');
    // Находим внутри этого контейнера поле input
    const passInput = container.querySelector('input[type="password"], input[type="text"]');

    if (checkbox.checked) {
        passInput.type = 'text';
    } else {
        passInput.type = 'password';
    }
}