from django.db import models

class JobRecord(models.Model):
    job_id       = models.CharField(max_length=64, unique=True)
    input_name   = models.CharField(max_length=512)
    input_ext    = models.CharField(max_length=16)
    output_format= models.CharField(max_length=16)
    strategy     = models.CharField(max_length=128, blank=True)
    status       = models.CharField(max_length=32)  # done/error/cancelled
    file_size    = models.BigIntegerField(default=0)
    created_at   = models.FloatField()  # unix timestamp
    completed_at = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']


class Visitor(models.Model):
    visitor_id   = models.CharField(max_length=64, unique=True)  # UUID stored in cookie
    first_seen   = models.FloatField()   # unix timestamp
    last_seen    = models.FloatField()   # unix timestamp
    visit_count  = models.IntegerField(default=1)

    class Meta:
        ordering = ['-last_seen']

class UserAccount(models.Model):
    visitor_id        = models.CharField(max_length=64, unique=True)
    credits           = models.IntegerField(default=0)
    free_used_month   = models.IntegerField(default=0)  # conversions used this month
    free_reset_month  = models.CharField(max_length=7, default='')  # e.g. '2026-03'

    class Meta:
        ordering = ['-id']

    def get_free_remaining(self):
        from django.conf import settings
        import datetime
        current_month = datetime.date.today().strftime('%Y-%m')
        if self.free_reset_month != current_month:
            # New month — reset counter
            self.free_used_month = 0
            self.free_reset_month = current_month
            self.save(update_fields=['free_used_month', 'free_reset_month'])
        return max(0, settings.FREE_MONTHLY_CONVERSIONS - self.free_used_month)

    def can_convert(self, file_size_bytes):
        """Returns (allowed: bool, reason: str, is_paid: bool)"""
        from django.conf import settings
        size_mb = file_size_bytes / (1024 * 1024)
        free_remaining = self.get_free_remaining()

        if self.credits > 0:
            # Paid user — check paid file size limit
            if size_mb > settings.PAID_MAX_FILE_SIZE_MB:
                return False, f'File too large. Max {settings.PAID_MAX_FILE_SIZE_MB}MB for paid users.', True
            return True, '', True
        elif free_remaining > 0:
            # Free user — check free file size limit
            if size_mb > settings.FREE_MAX_FILE_SIZE_MB:
                return False, f'Free tier: max {settings.FREE_MAX_FILE_SIZE_MB}MB per file. Buy credits for larger files.', False
            return True, '', False
        else:
            return False, 'You have used your 3 free conversions this month. Buy credits to continue.', False
        

class CreditOrder(models.Model):
    STATUS_CHOICES = [
        ('pending',  'Pending'),
        ('paid',     'Paid'),
        ('failed',   'Failed'),
        ('expired',  'Expired'),
    ]

    visitor_id      = models.CharField(max_length=64, db_index=True)
    package_key     = models.CharField(max_length=32)   # 'starter' / 'standard' / 'pro'
    credits         = models.IntegerField()
    amount_centavos = models.IntegerField()              # e.g. 4900 = ₱49.00
    paymongo_source_id = models.CharField(max_length=128, blank=True)
    status          = models.CharField(max_length=16, choices=STATUS_CHOICES, default='pending')
    created_at      = models.DateTimeField(auto_now_add=True)
    paid_at         = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']