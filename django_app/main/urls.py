from django.urls import path
from . import views

app_name = 'main'

urlpatterns = [
    path('', views.index, name='index'),
    path('camera', views.camera, name='camera'),
    path('create_camera', views.create_camera, name='create_camera'),
    path('delete_camera/<int:camera_id>', views.delete_camera, name='delete_camera'),
    path('edit_camera/<int:camera_id>', views.edit_camera, name='edit_camera'),
    path('get-stream/<int:camera_id>', views.get_camera_stream_url, name='get-stream'),
    path('api/ping-server/', views.mediamtx_ping, name='ping_server'),
    path('api/ensure_camera/<int:camera_id>', views.ensure_camera_in_mediamtx, name='ensure_camera'),
    # path('api/check_camera_network/<int:camera_id>', views.check_camera_network, name='check_camera_network'),
]
