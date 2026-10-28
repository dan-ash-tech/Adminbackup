"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path, reverse_lazy
from django.views.generic.base import RedirectView
from Adminbackup import views

urlpatterns = [
    path(
        "",
        RedirectView.as_view(url=reverse_lazy("login"), permanent=False),
        name="home",
    ),
    path(
        "account/",
        RedirectView.as_view(pattern_name="dashboard", permanent=False),
        name="account",
    ),
    path("accounts/", include("django.contrib.auth.urls")    ),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("activity-logs/", views.activity_logs, name="activity-logs"),
    path(
        "dashboard/export.xlsx",
        views.export_dashboard_targets,
        name="dashboard-export",
    ),
    path(
        "targets/<int:case_id>/view/",
        views.view_target,
        name="view-target",
    ),
    path("users/new/", views.create_users, name="create-users"),
    path("users/", views.manage_users, name="manage-users"),
    path("users/<int:user_id>/edit/", views.edit_user, name="edit-user"),
    path(
        "users/<int:user_id>/reset-password/",
        views.reset_user_password,
        name="reset-user-password",
    ),
    path("users/<int:user_id>/delete/", views.delete_user, name="delete-user"),
    path(
        "cases/<int:case_id>/phone-numbers/<int:phone_number_id>/delete/",
        views.delete_case_phone_number,
        name="delete-case-phone-number",
    ),
    path(
        "targets/<int:case_id>/phone-numbers/edit/",
        views.edit_target_phone_numbers,
        name="edit-target-phone-numbers",
    ),
    path(
        "targets/<int:case_id>/delete/",
        views.delete_target,
        name="delete-target",
    ),
    path("cases/new/", views.submit_case, name="submit-case"),
    path(
        "targets/<int:case_id>/edit/",
        views.edit_target,
        name="edit-target",
    ),
    path(
        "cases/submitted/",
        views.case_submission_complete,
        name="case-submission-complete",
    ),
    path(
        'media/personal_identifications/<path:filename>',
        views.private_identification_media,
        name='private-identification-media',
    ),
    path(
        "targets/<int:case_id>/files/<str:field_name>/",
        views.private_target_file,
        name="private-target-file",
    ),
    path(
        "targets/<int:case_id>/reports/<int:report_id>/",
        views.private_attached_report,
        name="private-attached-report",
    ),
    path("admin/", admin.site.urls),
]
