"""为2.10实验副本应用窄范围本地回移；不是声称上游补丁原样适用。"""
from pathlib import Path
import sys
root=Path(sys.argv[1])
p=root/'src/rsn_supp/pmksa_cache.c';s=p.read_text();start=s.index('void pmksa_cache_flush(');end=s.index('\n\n/**',start);fn=s[start:end]
fn=fn.replace('void pmksa_cache_flush(struct rsn_pmksa_cache *pmksa, void *network_ctx,\n\t\t       const u8 *pmk, size_t pmk_len, bool external_only)','void pmksa_cache_flush_addr(struct rsn_pmksa_cache *pmksa, void *network_ctx, const u8 *aa)')
a=fn.index('\t\tif (');b=fn.index(' {',a)
fn=fn[:a]+'\t\tif (aa && entry->network_ctx == network_ctx && os_memcmp(aa, entry->aa, ETH_ALEN) == 0)'+fn[b:]
s=s[:end]+'\n\n'+fn+s[end:];p.write_text(s)
p=root/'src/rsn_supp/wpa.c';s=p.read_text();s+='''
/* ReconnectGuard experimental address-scoped backport for hostap 2.10. */
void pmksa_cache_flush_addr(struct rsn_pmksa_cache *, void *, const u8 *);
void wpa_sm_pmksa_cache_flush_addr(struct wpa_sm *sm, void *network_ctx, const u8 *aa)
{
    pmksa_cache_flush_addr(sm->pmksa, network_ctx, aa);
}
''';p.write_text(s)
p=root/'wpa_supplicant/events.c';s=p.read_text();marker='static void wpas_event_deauth(struct wpa_supplicant *wpa_s,';assert marker in s
s=s.replace(marker,'void wpa_sm_pmksa_cache_flush_addr(struct wpa_sm *, void *, const u8 *);\n\n'+marker,1)
pos=s.index('\twpa_reset_ft_completed(wpa_s->wpa);',s.index(marker));pos+=len('\twpa_reset_ft_completed(wpa_s->wpa);')
s=s[:pos]+'''
#if defined(CONFIG_SAE) && defined(CONFIG_SME)
    /* Only reason 9 while associating via cached SAE, from the current AP. */
    if (info && !info->locally_generated && info->reason_code == 9 &&
        wpa_s->wpa_state == WPA_ASSOCIATING && wpa_s->sme.sae_pmksa_caching &&
        wpa_s->current_ssid && wpa_s->current_bss &&
        info->addr && os_memcmp(info->addr, wpa_s->current_bss->bssid, ETH_ALEN) == 0 &&
        wpa_key_mgmt_sae(wpa_s->current_ssid->key_mgmt)) {
        wpa_dbg(wpa_s, MSG_DEBUG, "RG: scoped SAE PMKSA deauth fallback");
        wpa_sm_aborted_cached(wpa_s->wpa);
        wpa_sm_pmksa_cache_flush_addr(wpa_s->wpa, wpa_s->current_ssid,
                                     wpa_s->current_bss->bssid);
    }
#endif
'''+s[pos:];p.write_text(s)
