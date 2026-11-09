import re

from django.core.exceptions import ValidationError

RESERVED = {'root', 'daemon', 'bin', 'sys', 'sync', 'games', 'man', 'lp', 'mail', 'news', 'uucp',
            'proxy', 'www-data', 'backup', 'list', 'irc', 'gnats', 'nobody', '_apt', 'sshd',
            'systemd-network', 'systemd-resolve', 'messagebus', 'polkitd'}
RESERVED |= {'adm', 'tty', 'disk', 'kmem', 'dialout', 'fax', 'voice', 'cdrom', 'floppy', 'tape',
            'sudo', 'audio', 'dip', 'video', 'plugdev', 'staff', 'users', 'netdev', 'crontab', 'ssl-cert', 'shadow', 'docker'}


def validate_login_name(name):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,31}', name) or name.lower() in RESERVED:
        raise ValidationError('用户名需为 1–32 位字母、数字、下划线、短横线或点，以字母或下划线开头，并避开系统账号名称。')
    return name
