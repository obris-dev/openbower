from openbower_kernel.urls import api_path

from . import views

urlpatterns = [
    api_path("", views.ListsView.as_view(), name="lists_index"),
    api_path("import", views.ListImportView.as_view(), name="lists_import"),
    api_path("folders", views.FoldersView.as_view(), name="lists_folders"),
    api_path("folders/<str:id>", views.FolderDetailView.as_view(), name="lists_folder_detail"),
    api_path("<str:id>", views.ListDetailView.as_view(), name="lists_detail"),
    api_path("<str:id>/rows", views.ListRowsView.as_view(), name="lists_rows"),
]
