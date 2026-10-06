from scripts.install_watchtower_supervisor import ALLOWED,remove
def test_only_two_structural_stanzas_are_removed():
 source='[program:watchtower_api]\na=1\n[program:watchtower_listener]\na=2\n[program:operation_monitor_worker]\na=3\n'
 out=remove(remove(source,'watchtower_api'),'operation_monitor_worker');assert '[program:watchtower_listener]' in out and all(f'[program:{x}]' not in out for x in ALLOWED)
