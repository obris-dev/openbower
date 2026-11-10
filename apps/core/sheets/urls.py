from openbower_kernel.urls import api_path

from . import views

urlpatterns = [
    api_path("", views.SheetsView.as_view(), name="sheets_index"),
    api_path("import", views.SheetImportView.as_view(), name="sheets_import"),
    api_path("folders", views.FoldersView.as_view(), name="sheets_folders"),
    api_path("folders/<str:id>", views.FolderDetailView.as_view(), name="sheets_folder_detail"),
    api_path("<str:id>", views.SheetDetailView.as_view(), name="sheets_detail"),
    api_path("<str:id>/rows", views.SheetRowsView.as_view(), name="sheets_rows"),
    api_path("<str:id>/rows/<str:row_id>", views.SheetRowView.as_view(), name="sheets_row"),
    api_path("<str:id>/ingest", views.SheetIngestView.as_view(), name="sheets_ingest"),
    api_path("<str:id>/columns", views.ColumnsView.as_view(), name="sheets_columns"),
    api_path("<str:id>/columns/ai", views.AiColumnView.as_view(), name="sheets_columns_ai"),
    api_path("<str:id>/columns/webhook", views.ColumnWebhookView.as_view(), name="sheets_columns_webhook"),
    api_path(
        "<str:id>/columns/webhook/test", views.ColumnWebhookTestView.as_view(), name="sheets_columns_webhook_test"
    ),
    api_path(
        "<str:id>/columns/webhook/preview",
        views.ColumnWebhookPreviewView.as_view(),
        name="sheets_columns_webhook_preview",
    ),
    api_path("<str:id>/column-order", views.ColumnOrderView.as_view(), name="sheets_columns_order"),
    api_path("<str:id>/columns/<str:key>", views.ColumnDetailView.as_view(), name="sheets_column_detail"),
    api_path("<str:id>/columns/<str:key>/fill", views.ColumnFillView.as_view(), name="sheets_column_fill"),
    api_path("<str:id>/columns/<str:key>/prompt", views.ColumnPromptView.as_view(), name="sheets_column_prompt"),
    api_path(
        "<str:id>/columns/<str:key>/webhook",
        views.ColumnWebhookDetailView.as_view(),
        name="sheets_column_webhook_config",
    ),
    api_path("<str:id>/fills", views.SheetFillsView.as_view(), name="sheets_fills"),
    api_path("<str:id>/fills/<str:fill_run_id>/cancel", views.FillCancelView.as_view(), name="sheets_fill_cancel"),
]
