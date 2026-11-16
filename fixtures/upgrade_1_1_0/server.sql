BEGIN TRANSACTION;
CREATE TABLE codes (code_hash TEXT PRIMARY KEY,
                    expires_at TEXT NOT NULL, remaining INTEGER NOT NULL CHECK(remaining >= 0));
CREATE TABLE commands (
                command_id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(device_id),
                action TEXT NOT NULL CHECK(action IN ('sleep','restart','shutdown','refresh')),
                status TEXT NOT NULL CHECK(status IN ('pending','delivered','done','failed','cancelled','expired')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE devices (
                    device_id TEXT PRIMARY KEY, identity TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','approved','denied','revoked')),
                    credential_hash TEXT, policy_version INTEGER NOT NULL,
                    policy TEXT NOT NULL, created_at TEXT NOT NULL,
                    last_received_at TEXT, latest_seq INTEGER NOT NULL DEFAULT 0,
                    latest_record TEXT, history TEXT, history_received_at TEXT,
                    readings TEXT, readings_received_at TEXT, retired_seq INTEGER NOT NULL DEFAULT 0, retained_before TEXT, search_text TEXT NOT NULL DEFAULT '', group_name TEXT NOT NULL DEFAULT '', refresh_requested INTEGER NOT NULL DEFAULT 0 CHECK(refresh_requested IN (0,1)), client_version TEXT, health TEXT, health_received_at TEXT);
INSERT INTO "devices" VALUES('aa870c6f-be61-4974-8e39-b81dde225876','{"asset_tag":"LT-0042","department":"Kế toán","deployment_date":"","display_name":"Máy kế toán 01","location":""}','approved','c6524467dfef7deb97b9d8924da9f103130bd57b940146cba37bc55a46779250',2,'{"control":false,"health":false,"history":true,"resources":true}','2026-11-16T05:40:45.403931+00:00','2026-11-16T05:40:45.604115+00:00',5,'{"awake_seconds":30.0,"imported_seconds":0,"measured_seconds":30.0,"observed_at":"2026-11-16T05:40:49.507968+00:00","seq":5,"totals":{"baseline_seconds":0.0,"imported_seconds":0.0,"measured_seconds":150.0}}','{"daily":{"2026-11-16":150.0},"daily_estimated":false,"first_seen":null,"first_seen_source":"unknown","imported_daily":{},"recording_started":"2026-11-16T05:40:45.507968+00:00"}','2026-11-16T05:40:45.604115+00:00','{"observed_at":"2026-11-16T05:40:49.507968+00:00","performance":{"cpu_percent":12,"disk_free_bytes":137438953472,"ram_used_bytes":2147483648},"specs":{"battery_design_wh":null,"cpu_cores":4,"cpu_name":"Example CPU","cpu_threads":8,"disks":[{"free_bytes":137438953472,"name":"C:","total_bytes":274877906944}],"gpu_names":["Example graphics"],"mac_addresses":["02:00:00:00:00:01"],"manufacturer":"Demo","model":"Upgrade test","os_name":"Windows","os_version":"11","ram_bytes":8589934592},"system":"Windows"}','2026-11-16T05:40:45.604115+00:00',0,NULL,'aa870c6f-be61-4974-8e39-b81dde225876
máy kế toán 01
lt-0042
kế toán

','kế toán',0,'1.1.0',NULL,NULL);
CREATE TABLE events (event_id INTEGER PRIMARY KEY, device_id TEXT,
                kind TEXT NOT NULL, time TEXT NOT NULL);
INSERT INTO "events" VALUES(1,'aa870c6f-be61-4974-8e39-b81dde225876','invitation-used','2026-11-16T05:40:45.403931+00:00');
INSERT INTO "events" VALUES(2,'aa870c6f-be61-4974-8e39-b81dde225876','enrollment-pending','2026-11-16T05:40:45.403931+00:00');
CREATE TABLE guess_ips (ip_hash TEXT PRIMARY KEY, failures INTEGER NOT NULL,
                next_attempt TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE invitations (invitation_id TEXT PRIMARY KEY,
                code_hash TEXT NOT NULL, secret_hash TEXT NOT NULL, expires_at TEXT NOT NULL,
                remaining INTEGER NOT NULL CHECK(remaining >= 0), failures INTEGER NOT NULL DEFAULT 0,
                revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)), created_at TEXT NOT NULL);
INSERT INTO "invitations" VALUES('YV20S','a692792baae95b6f64aa93b47bf8ada002a8f2b26722f0ea77ce9ddfef84872d','0be1b4187c07e56d6a284003c183337a41f2a0ceb189e5994f64aa936b1814c3','2026-11-17T05:40:45.253419+00:00',0,0,0,'2026-11-16T05:40:45.253419+00:00');
CREATE TABLE performance_samples (
                device_id TEXT NOT NULL REFERENCES devices(device_id), observed_at TEXT NOT NULL,
                cpu_percent REAL, ram_percent REAL, disk_free_percent REAL, battery_percent REAL,
                power_watts REAL, temperature_c REAL, PRIMARY KEY(device_id,observed_at));
INSERT INTO "performance_samples" VALUES('aa870c6f-be61-4974-8e39-b81dde225876','2026-11-16T05:40:46.507968+00:00',12.0,25.0,50.0,NULL,NULL,NULL);
CREATE TABLE records (device_id TEXT NOT NULL REFERENCES devices(device_id),
                    seq INTEGER NOT NULL CHECK(seq >= 1), digest TEXT NOT NULL,
                    observed_at TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL, awake_seconds REAL NOT NULL DEFAULT 0 CHECK(awake_seconds >= 0), measured_seconds REAL NOT NULL DEFAULT 0 CHECK(measured_seconds >= 0), imported_seconds REAL NOT NULL DEFAULT 0 CHECK(imported_seconds >= 0),
                    PRIMARY KEY(device_id, seq));
INSERT INTO "records" VALUES('aa870c6f-be61-4974-8e39-b81dde225876',1,'e6eb451d1763f8e9a0d2bd91d1d87fa69eadb0e0e54d819e7684b023d9e13795','2026-11-16T05:40:45.507968+00:00','2026-11-16T05:40:45.543336+00:00','{"awake_seconds":30.0,"imported_seconds":0,"measured_seconds":30.0,"observed_at":"2026-11-16T05:40:45.507968+00:00","seq":1,"totals":{"baseline_seconds":0.0,"imported_seconds":0.0,"measured_seconds":30.0}}',30.0,30.0,0.0);
INSERT INTO "records" VALUES('aa870c6f-be61-4974-8e39-b81dde225876',2,'51f9f2c54c6296cd2c615088042d470070f85219afbe6bfda467db7b337353cd','2026-11-16T05:40:46.507968+00:00','2026-11-16T05:40:45.543336+00:00','{"awake_seconds":30.0,"imported_seconds":0,"measured_seconds":30.0,"observed_at":"2026-11-16T05:40:46.507968+00:00","seq":2,"totals":{"baseline_seconds":0.0,"imported_seconds":0.0,"measured_seconds":60.0}}',30.0,30.0,0.0);
INSERT INTO "records" VALUES('aa870c6f-be61-4974-8e39-b81dde225876',3,'9afb727a752f548dfee92bd85df31acef774671ab490b4c2f8a58e49e7e7e76b','2026-11-16T05:40:47.507968+00:00','2026-11-16T05:40:45.558760+00:00','{"awake_seconds":30.0,"imported_seconds":0,"measured_seconds":30.0,"observed_at":"2026-11-16T05:40:47.507968+00:00","seq":3,"totals":{"baseline_seconds":0.0,"imported_seconds":0.0,"measured_seconds":90.0}}',30.0,30.0,0.0);
INSERT INTO "records" VALUES('aa870c6f-be61-4974-8e39-b81dde225876',4,'990fd73f20733cc78e48d57d0faf33431be548006b6bd775717f05910522e52e','2026-11-16T05:40:48.507968+00:00','2026-11-16T05:40:45.573596+00:00','{"awake_seconds":30.0,"imported_seconds":0,"measured_seconds":30.0,"observed_at":"2026-11-16T05:40:48.507968+00:00","seq":4,"totals":{"baseline_seconds":0.0,"imported_seconds":0.0,"measured_seconds":120.0}}',30.0,30.0,0.0);
INSERT INTO "records" VALUES('aa870c6f-be61-4974-8e39-b81dde225876',5,'942eaef2dbc3126a5ed693fdf71ee7ace204a063aa7df204c0e50c5c13003380','2026-11-16T05:40:49.507968+00:00','2026-11-16T05:40:45.604115+00:00','{"awake_seconds":30.0,"imported_seconds":0,"measured_seconds":30.0,"observed_at":"2026-11-16T05:40:49.507968+00:00","seq":5,"totals":{"baseline_seconds":0.0,"imported_seconds":0.0,"measured_seconds":150.0}}',30.0,30.0,0.0);
CREATE TABLE requests (request_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL REFERENCES devices(device_id),
                    request_digest TEXT NOT NULL, poll_hash TEXT, policy TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('pending','approved','denied','revoked','completed')),
                    created_at TEXT NOT NULL);
INSERT INTO "requests" VALUES('96181f71-f37f-4e17-9499-dfaa4a902c0c','aa870c6f-be61-4974-8e39-b81dde225876','d0edc1c239131813364076e56d769b37cc666e0ba5fdd51aa0f12b532ce269cb',NULL,'{"control":false,"health":false,"history":true,"resources":true}','completed','2026-11-16T05:40:45.403931+00:00');
CREATE TABLE settings (singleton INTEGER PRIMARY KEY CHECK(singleton=1), retained_before TEXT, reporting_interval INTEGER NOT NULL DEFAULT 30 CHECK(reporting_interval BETWEEN 5 AND 3600), organisation TEXT NOT NULL DEFAULT '');
INSERT INTO "settings" VALUES(1,NULL,30,'');
CREATE TABLE throttle (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    window_start TEXT NOT NULL, failures INTEGER NOT NULL CHECK(failures >= 0));
CREATE INDEX records_observed ON records(observed_at);
CREATE INDEX requests_device ON requests(device_id);
CREATE INDEX records_usage ON records(device_id,awake_seconds,measured_seconds,imported_seconds);
CREATE INDEX devices_order ON devices(created_at,device_id);
CREATE INDEX devices_status ON devices(status,created_at,device_id);
CREATE INDEX devices_group ON devices(group_name,created_at,device_id);
CREATE INDEX performance_device_time ON performance_samples(device_id,observed_at);
CREATE UNIQUE INDEX commands_active ON commands(device_id) WHERE status IN ('pending','delivered');
COMMIT;
PRAGMA application_id=1280593221;
PRAGMA user_version=7;
