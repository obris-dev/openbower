from openbower_kernel.urls import api_path

from . import views

urlpatterns = [
    api_path("", views.ListsView.as_view(), name="lists_index"),
    api_path("import", views.ListImportView.as_view(), name="lists_import"),
    api_path("folders", views.FoldersView.as_view(), name="lists_folders"),
    api_path("folders/<str:id>", views.FolderDetailView.as_view(), name="lists_folder_detail"),
    api_path("<str:id>", views.ListDetailView.as_view(), name="lists_detail"),
    api_path("<str:id>/rows", views.ListRowsView.as_view(), name="lists_rows"),
    api_path("<str:id>/ingest", views.ListIngestView.as_view(), name="lists_ingest"),
    api_path("<str:id>/columns", views.ColumnsView.as_view(), name="lists_columns"),
    api_path("<str:id>/columns/ai", views.AiColumnView.as_view(), name="lists_columns_ai"),
    api_path("<str:id>/columns/webhook", views.ColumnWebhookView.as_view(), name="lists_columns_webhook"),
    api_path("<str:id>/columns/webhook/test", views.ColumnWebhookTestView.as_view(), name="lists_columns_webhook_test"),
    api_path(
        "<str:id>/columns/webhook/preview",
        views.ColumnWebhookPreviewView.as_view(),
        name="lists_columns_webhook_preview",
    ),
    api_path("<str:id>/column-order", views.ColumnOrderView.as_view(), name="lists_columns_order"),
    api_path("<str:id>/columns/<str:key>", views.ColumnDetailView.as_view(), name="lists_column_detail"),
    api_path("<str:id>/columns/<str:key>/fill", views.ColumnFillView.as_view(), name="lists_column_fill"),
    api_path("<str:id>/columns/<str:key>/prompt", views.ColumnPromptView.as_view(), name="lists_column_prompt"),
    api_path(
        "<str:id>/columns/<str:key>/webhook",
        views.ColumnWebhookDetailView.as_view(),
        name="lists_column_webhook_config",
    ),
    api_path("<str:id>/fills", views.ListFillsView.as_view(), name="lists_fills"),
    api_path("<str:id>/fills/<str:fill_run_id>/cancel", views.FillCancelView.as_view(), name="lists_fill_cancel"),
]
