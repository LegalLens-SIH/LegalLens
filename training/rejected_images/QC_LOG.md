# LegalLense raw_images QC Log

Automated QC pass over `training/raw_images/`. Originals only ever MOVED
(never copied/renamed/resized/modified) when rejected. Kept images remain
in `training/raw_images/` untouched.

## Session: 2026-09-10 — full raw_images batch (36 images inspected)

| Image | Decision | Reason |
|---|---|---|
| 5star_back.jpg | KEPT | Real photo, rotated 90°; mrp, unit_sale_price, net_quantity, entity_details, product_identity legible |
| Azithromycin_strip.avif | REJECTED | Wrong domain (Schedule H pharmaceutical strip, not a Legal Metrology packaged commodity); no mrp/net_quantity/dates visible either |
| Bourbon_back.jpg | KEPT | Crease across label and cluttered background, but entity_details and consumer_care remain legible above/around the crease |
| Bournvita_back.jpg | KEPT | mfg_date_batch, entity_details, consumer_care, unit_sale_price legible; MRP text cut at frame edge |
| Butter_back.jpg | KEPT | entity_details and consumer_care fully legible; MRP/date/batch are on a different panel not in this photo (pack's own text says "see side panel") |
| Chips_back.jpg | KEPT | Real hand-held photo; mrp (₹10.00), entity_details, consumer_care legible |
| Chips_nutrition.jpg | KEPT | Close-up companion crop of Chips_back.jpg (same product — keep together in same split); reveals legible country_of_origin ("PRODUCT OF INDIA") not readable in the wider shot |
| Chocolate_back.jpg | KEPT | entity_details and consumer_care legible; net_quantity/mrp/mfg_date_batch value areas blank |
| Coffee_back.jpg | KEPT | Zoomed crop; entity_details and consumer_care legible; FSSAI license number blanked |
| Coffee_front.jpg | KEPT | Front-of-pack studio shot; only weak coverage (product_identity via "Soluble Coffee Powder" badge), no other declarations present — same product set as Coffee_back.jpg/Coffee_sides.jpg |
| Coffee_sides.jpg | KEPT | Same product as Coffee_back.jpg/Coffee_front.jpg (keep together in split); entity_details, consumer_care, net_quantity (500g) legible |
| Colgate_back.jpg | KEPT | net_quantity, country_of_origin, entity_details legible; studio-style lighting but real product; no absolute MRP amount printed |
| Colgate_back.png | REJECTED | Pixel-diff confirms near-identical content to Colgate_back.jpg (same source image, mean diff ~1.4/255) — duplicate with no additional visual variation |
| Combiflam_back.avif | REJECTED | Wrong domain (Schedule H pharmaceutical box); faint third-party watermark visible; MRP/batch/mfg/expiry value areas blank |
| Cookie_back.jpg | KEPT | entity_details, consumer_care, net_quantity, country_of_origin ("MADE IN INDIA") legible |
| Dark chocolate_back.jpg | KEPT | Rotated 90°; net_quantity, mfg_date_batch, unit_sale_price, entity_details, consumer_care legible; MRP amount itself blank |
| DarkFantasy_back.jpg | KEPT | Embossed/stamped codes (batch, dates, price) moderately legible; entity_details, consumer_care, net_quantity clear |
| Facewash_back.jpg | KEPT | Extremely elongated aspect ratio (flag for pipeline handling); excellent coverage — mrp, unit_sale_price, mfg_date_batch, net_quantity, entity_details, consumer_care all legible |
| Ferrero_back.jpg | KEPT | Heavy glare/reflection streak obscures part of the text, but country_of_origin ("ITALY"), entity_details, net_quantity remain legible |
| GoodDay_back.jpg | KEPT | entity_details, consumer_care, net_quantity legible; MRP/date/batch area is a blank unstamped template box |
| Handwash_back.jpg | KEPT | Real hand-held photo; excellent coverage — mrp, unit_sale_price, mfg_date_batch, net_quantity, entity_details, consumer_care all legible |
| HighProteinPaneer_front.jpg | KEPT | net_quantity, entity_details, consumer_care legible; batch/date stamp partially legible |
| Icecream_back.jpg | KEPT | Extreme wide aspect ratio (flag for pipeline handling); entity_details, consumer_care, net_quantity legible; MRP/date/batch routed to "side panel" (genuine, not redacted) |
| Kajukatli_back.jpg | KEPT | Excellent coverage — mrp, unit_sale_price, net_quantity, mfg_date_batch, entity_details, consumer_care, country_of_origin all legible |
| Maggi_back.jpg | KEPT | Low resolution (374x534) but text still readable at full zoom; entity_details, consumer_care, net_quantity legible |
| Maggi_front.jpg | KEPT | Front-of-pack only; weak/minimal declaration content |
| Masala_back.jpg | REJECTED | Cooking-instructions/recipe panel only; no standalone legible LegalLense declaration region; redundant with Masala_front.jpg's product_identity coverage |
| Masala_front.jpg | KEPT | Front-of-pack only; weak coverage (product_identity via "Biryani Masala"); part of the Suhana Masala multi-angle set — keep together with side1/side2/top in the same split |
| Masala_side1.jpg | KEPT | Part of Suhana Masala set (keep with front/side2/top); country_of_origin ("PRODUCT OF INDIA") legible |
| Masala_side2.jpg | KEPT | Part of Suhana Masala set (keep with front/side1/top); entity_details, consumer_care legible |
| Masala_top.jpg | KEPT | Part of Suhana Masala set (keep with front/side1/side2); excellent stamped line — net_quantity, mfg_date_batch, mrp, unit_sale_price all legible |
| MilkyMistPaneer.jpg | KEPT | Same product as Paneer_front.jpg (keep together in same split); excellent — mrp, mfg_date_batch, net_quantity, entity_details, consumer_care all fully legible, none blanked |
| Paneer_front.jpg | KEPT | Same product as MilkyMistPaneer.jpg (keep together in same split); net_quantity, entity_details, consumer_care legible; MRP/batch/date areas blanked in this particular photo |
| ParleG_Back.jpg | KEPT | entity_details, consumer_care legible; MRP/net weight/date/batch value areas blanked |
| Pears_back.jpg | KEPT | Excellent coverage — entity_details, consumer_care, net_quantity, mrp (incl. a legible revised-MRP strikethrough), country_of_origin all legible |
| Salt_back.jpg | KEPT | entity_details, net_quantity, mrp, consumer_care legible; cross-promotional panel for other variants nearby — don't box that as this product's own fields |

## Earlier session (pre-existing in this folder, retained for reference)

| Image | Decision | Reason |
|---|---|---|
| Amoxicillin_back.avif | REJECTED | Drug label with blank "Non Varnishing Area" placeholder where MRP/batch/dates would print; visible third-party ("1mg") watermark; wrong domain |
| ChatGPT Image Sep 7, 2026, 07_03_18 PM.png | REJECTED | Corrupt/empty file (39 bytes), fails to open |
| ChatGPT Image Sep 7, 2026, 07_17_12 PM.png | REJECTED | Filename and studio-flat rendering indicate AI-generated/synthetic image, not a real photographed package |
| Lays.png | REJECTED | Studio-perfect rendering with no real-world imperfections; likely AI-generated/synthetic |
| dairmilk.png | REJECTED | Filename/content mismatch (labeled "dairmilk" but branded "SweetJoy"); likely AI-generated/synthetic, same profile as the ChatGPT-named file |
| nescafe.png | REJECTED | Studio-perfect rendering matching the same suspect canvas size as other synthetic files; likely AI-generated |
