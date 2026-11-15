"""对实验副本记录驱动确认的 PTK 与授权事件，不输出密钥。"""
from pathlib import Path
import sys
p=Path(sys.argv[1])/'src/drivers/driver_nl80211.c';s=p.read_text()
assert 'RG_AUTH' not in s
s=s.replace('#include "includes.h"','#include "includes.h"\n#include <time.h>',1)
marker='static int driver_nl80211_set_key(void *priv,'
a=s.index(marker);b=s.index('\n\nstatic int ',a+1);block=s[a:b]
block=block.replace('return wpa_driver_nl80211_set_key(bss, params);','''int rc;
    if (getenv("RG_AUTH_EARLY") && bss->drv->nlmode == NL80211_IFTYPE_AP &&
        params->alg == WPA_ALG_CCMP && params->key_idx == 0 && params->key_len > 0 && params->addr)
        wpa_driver_nl80211_sta_set_flags(priv, params->addr, WPA_STA_AUTHORIZED, WPA_STA_AUTHORIZED, ~0U);
    rc = wpa_driver_nl80211_set_key(bss, params);
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    wpa_printf(MSG_INFO, "RG_AUTH t=%ld.%09ld kind=key rc=%d alg=%d idx=%d len=%lu peer=" MACSTR,
               (long)ts.tv_sec, (long)ts.tv_nsec, rc, params->alg, params->key_idx,
               (unsigned long)params->key_len, MAC2STR(params->addr ? params->addr : (const u8 *)"\\0\\0\\0\\0\\0\\0"));
    return rc;''')
s=s[:a]+block+s[b:]
a=s.index('static int wpa_driver_nl80211_sta_set_flags(');b=s.index('\n\nstatic int ',a+1);block=s[a:b]
block=block.replace('return send_and_recv_msgs(bss->drv, msg, NULL, NULL, NULL, NULL);','''{
        int rc = send_and_recv_msgs(bss->drv, msg, NULL, NULL, NULL, NULL);
        struct timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        wpa_printf(MSG_INFO, "RG_AUTH t=%ld.%09ld kind=port rc=%d authorized=%d peer=" MACSTR,
                   (long)ts.tv_sec, (long)ts.tv_nsec, rc, !!(total_flags & WPA_STA_AUTHORIZED), MAC2STR(addr));
        return rc;
    }''')
s=s[:a]+block+s[b:]
a=s.index('static int wpa_driver_nl80211_set_supp_port(');b=s.index('\n\n/*',a+1);block=s[a:b]
needle='ret = send_and_recv_msgs(drv, msg, NULL, NULL, NULL, NULL);'
assert needle in block
block=block.replace(needle,needle+'''
    {
        struct timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        wpa_printf(MSG_INFO, "RG_AUTH t=%ld.%09ld kind=port rc=%d authorized=%d peer=" MACSTR,
                   (long)ts.tv_sec, (long)ts.tv_nsec, ret, authorized, MAC2STR(drv->bssid));
    }
''')
s=s[:a]+block+s[b:];p.write_text(s)
