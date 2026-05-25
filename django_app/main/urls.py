from django.urls import path
from . import views

app_name = 'main'

urlpatterns = [
    path('', views.index, name='index'),
    path('camera', views.camera, name='camera'),
    path('create_camera', views.create_camera, name='create_camera'),
    path('delete_camera/<int:camera_id>', views.delete_camera, name='delete_camera'),
    path('camera_records', views.all_cameras_archive_view, name='camera_records'),
    path('edit_camera/<int:camera_id>', views.edit_camera, name='edit_camera'),
    path('get-stream/<int:camera_id>', views.get_camera_stream_url, name='get-stream'),
    path('api/ping-server/', views.mediamtx_ping, name='ping_server'),
    path('api/ensure_camera/<int:camera_id>', views.ensure_camera_in_mediamtx, name='ensure_camera'),
    path('config', views.config_main, name='config'),
    path('api/bitrate/<int:camera_id>/', views.get_camera_bitrate, name='get_bitrate'),
    path('api/get_all_cameras_status/', views.get_all_cameras_status, name='get_all_cameras_status'),
    path('camera/<int:camera_id>/toggle-record/', views.toggle_record_view, name='toggle_record'),
    path('archive/webhook/record-created/', views.mediamtx_record_webhook, name='mediamtx_webhook'),
    path('archive/download/<int:record_id>/', views.download_record_view, name='download_record'),
    path('archive/delete/<int:record_id>/', views.delete_record_view, name='delete_record'),
    path('archive/webhook/record_stop/', views.mediamtx_record_stop_webhook, name='mediamtx_record_stop_webhook'),
]
