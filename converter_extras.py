from django import template
import datetime

register = template.Library()

@register.filter
def date_from_timestamp(value):
    try:
        return datetime.datetime.fromtimestamp(float(value)).strftime('%b %d, %Y')
    except:
        return '—'