from django.core.cache import cache
from .models import Camera

def update_camera_onvif_cache(camera):
    print(f'--- Пытаюсь обновить ONVIF (с опциями) для: {camera.name} ---')
    
    # Базовая структура по умолчанию
    data = {
        "status": "offline",
        "res": "—",
        "fps": "—",
        "supported": {
            "resolutions": [],
            "fps_range": {"min": 0, "max": 0}
        },
        "error": None
    }
    
    try:
        # 1. Получаем текущие данные
        info = camera.get_camera_info()
        
        if "error" not in info:
            data.update({
                "status": "online",
                "res": f"{info['resolution']['width']}x{info['resolution']['height']}",
                "fps": info['fps'],
            })
            
            # 2. Получаем поддерживаемые опции
            try:
                opts = camera.get_supported_options()
                fps_opts = camera.get_supported_fps_options()
                
                if "error" not in opts:
                    data["supported"]["resolutions"] = opts.get("resolutions", [])
                if "error" not in fps_opts:
                    data["supported"]["fps_range"] = {
                        "min": fps_opts.get("min"),
                        "max": fps_opts.get("max")
                    }
            except Exception as opt_err:
                print(f"--- Ошибка получения опций: {opt_err} ---")
            
            cache.set(f"cam_onvif_info_{camera.id}", data, None)
            print(f"--- Успешно обновлен кэш для {camera.name} ---")
        else:
            data["error"] = info['error']
            cache.set(f"cam_onvif_info_{camera.id}", data, None)
            
    except Exception as e:
        data["error"] = str(e)
        cache.set(f"cam_onvif_info_{camera.id}", data, None)
        print(f"--- КРИТИЧЕСКАЯ ОШИБКА: {str(e)} ---")