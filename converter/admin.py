from django.contrib import admin
from .models import JobRecord


@admin.register(JobRecord)
class JobRecordAdmin(admin.ModelAdmin):
    list_display  = ('input_name', 'input_ext', 'output_format', 'status', 'file_size_human', 'created_at_human')
    list_filter   = ('status', 'output_format', 'input_ext')
    search_fields = ('input_name', 'job_id')
    readonly_fields = ('job_id', 'created_at', 'completed_at')
    ordering      = ('-created_at',)

    def file_size_human(self, obj):
        size = obj.file_size
        for unit in ('B', 'KB', 'MB', 'GB'):
            if size < 1024:
                return f'{size:.1f} {unit}'
            size /= 1024
        return f'{size:.1f} TB'
    file_size_human.short_description = 'File size'

    def created_at_human(self, obj):
        import datetime
        return datetime.datetime.fromtimestamp(obj.created_at).strftime('%Y-%m-%d %H:%M:%S')
    created_at_human.short_description = 'Created at'