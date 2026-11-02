# هوش بازار سرمایه

سامانه محلی برای جمع‌آوری داده زنده، نگاشت خبر به نماد، ثبت پیش‌بینی و سنجش بعدی آن است.

```bash
PYTHONPATH=src python3 -m bourse_intel cycle --enrich-limit 80
PYTHONPATH=src python3 -m bourse_intel report --slot adhoc
PYTHONPATH=src python3 -m bourse_intel serve --port 8765
PYTHONPATH=src python3 -m bourse_intel watch --interval 900
```

پایش دوره‌ای، از جمله پنجشنبه و جمعه، با `watch` انجام می‌شود. اگر ساعت تهران ۸:۵۰ یا ۱۹:۰۰ باشد گزارش `preopen` یا `evening` هم ساخته می‌شود.

داده آزمایشی فقط با این دستور و فقط داخل `demo.db` نوشته می‌شود:

```bash
PYTHONPATH=src python3 -m bourse_intel --db data/demo.db demo
```

فایل `tenant_72990.txt` پایگاه حسابداری دیگری است و این سامانه آن را نمی‌خواند و تغییر نمی‌دهد.

وضعیت اتصال در این محیط، که هر چرخه دوباره در جدول `collector_runs` ثبت می‌شود:

- تابلوی ره‌آورد، RSS دنیای اقتصاد و فردای اقتصاد، صفحه بورس۲۴ و پیش‌نمایش عمومی تلگرام پاسخ می‌دهند.
- TSETMC، کدال و فیپیران از این محیط تایم‌اوت می‌شوند. تا وقتی جواب ندهند هیچ ردیفی از آن‌ها ساخته نمی‌شود.
- داده آزمایشی وارد `data/intel.db` نمی‌شود.
