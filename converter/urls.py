from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('upload/', views.upload, name='upload'),
    path('status/<str:job_id>/', views.status, name='status'),
    path('download/<str:job_id>/', views.download, name='download'),
    path('pause/<str:job_id>/', views.pause_job, name='pause'),
    path('cancel/<str:job_id>/', views.cancel_job, name='cancel'),
    path('cleanup/<str:job_id>/', views.cleanup, name='cleanup'),
    path('active-job/<str:job_id>/', views.active_job, name='active_job'),  # NEW
    path('analytics/', views.analytics_dashboard, name='analytics'),
    path('analytics/api/',      views.analytics_api,       name='analytics_api'),
    path('analytics/login/', views.analytics_login, name='analytics_login'),
    path('analytics/logout/', views.analytics_logout, name='analytics_logout'),
    path('pricing/', views.pricing, name='pricing'),
    path('payment/create/',   views.payment_create,   name='payment_create'),
    path('payment/success/',  views.payment_success,  name='payment_success'),
    path('payment/failed/',   views.payment_failed,   name='payment_failed'),
    path('payment/webhook/',  views.payment_webhook,  name='payment_webhook'),
]
