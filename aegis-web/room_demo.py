from inspection import stamp
import wifi

SSID="AGH-LAB"
BASE="02:aa:00:00:00:01"
EXTRA="02:aa:00:00:00:02"


def run(db, incident=False):
    db.execute("BEGIN IMMEDIATE")
    db.execute("INSERT OR IGNORE INTO wifi_inventory VALUES (?,?,?)",(SSID,BASE,"802.1X"))
    db.execute("INSERT OR IGNORE INTO inventory_zones VALUES (?,?,?)",(SSID,BASE,"A"))
    variation=db.execute("SELECT COUNT(*) / 4 FROM wifi_scans").fetchone()[0] % 2
    for n,(zone,pct) in enumerate((("A",44),("B",86),("C",66),("D",30))):
        at=stamp()
        points=[(SSID,BASE,"WPA2-ENTERPRISE",77-n,6)]
        if incident:
            points.append((SSID,EXTRA,"OPEN",pct+variation,11))
        wifi.save_scan(db,"jury-"+zone,SSID,points,at,{"location":{"source":"none"}},zone=zone,sensor="Demo "+zone)
