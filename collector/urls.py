from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('execute/', views.execute_command, name='execute_command'),
    path('files/', views.collected_files, name='collected_files'),
    path('view/<path:file_path>/', views.view_file, name='view_file'),
    path('dump/<str:dump_name>/', views.view_dump, name='view_dump'),
    path('decode/<path:file_path>/', views.decode_backup, name='decode_backup'),
    path('decode-whatsapp/<path:file_path>/', views.decode_whatsapp, name='decode_whatsapp'),
    path('preview-table/<path:db_path>/<str:table_name>/', views.preview_table, name='preview_table'),
    
    # Nouvelles routes
    path('system-architecture/', views.system_architecture, name='system_architecture'),
    path('get-system-info/', views.get_system_info, name='get_system_info'),
    path('explore-root/', views.explore_root, name='explore_root'),
    path('extract-calls/', views.extract_calls_view, name='extract_calls'),
        # Nouvelles routes pour la chaîne de preuve
    path('verify-integrity/<path:file_path>/', views.verify_file_integrity, name='verify_integrity'),
    path('evidence-chain/', views.view_evidence_chain, name='evidence_chain'),
    path('generate-report/', views.generate_forensic_report_view, name='generate_report'),
    path('download-report/<str:report_id>/', views.download_report, name='download_report'),
]