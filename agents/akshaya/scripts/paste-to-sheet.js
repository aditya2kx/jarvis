async (page) => {
  const tsvData = `AKSHAYA \u2014 HQ Inventory Forecast\t\t\t\t\t\t\t
Last updated: 2026-04-16\t\t\t\t\t\t\t
\t\t\t\t\t\t\t
CONFIG\t\t\t\t\t\t\t
Weekly Growth Rate (%)\t5\t\u2190 Change this to your expected weekly growth %\t\t\t\t\t
Months of HQ Buffer\t2.5\t\u2190 How many months of HQ inventory to keep on hand\t\t\t\t\t
Current Daily Avg Orders\t41.9\t\u2190 From 20 days of data\t\t\t\t\t
\t\t\t\t\t\t\t
WEEKLY ORDER VOLUME\t\t\t\t\t\t\t
Week Starting\tDays\tTotal Orders\tDaily Avg\tWoW Growth\t\t\t
2026-03-23\t7\t325\t46.4\tN/A\t\t\t
2026-03-30\t7\t231\t33.0\t-28.9%\t\t\t
2026-04-06\t6\t282\t47.0\t+42.4%\t\t\t
\t\t\t\t\t\t\t
\t\t\t\t\t\t\t
HQ ITEM FORECAST\t\t\t\t\t\t\t
Inventory as of: 2026-04-15\t\t\t\t\t\t\t
Item\tType\tCurrent Stock\tAvg Use/Day\tDays of Supply (current)\tDays of Supply (with growth)\tNeed for Buffer Period\tOrder Quantity
A\u00e7a\u00ed\tBase\t23.6\t1.55\t15\t13\t151.7\t128.1
Coconut\tBase\t12.0\t5.04\t2\t2\t493.7\t481.7
Mango\tBase\t13.7\t0.55\t25\t21\t54.3\t40.6
Pitaya\tBase\t12.2\t0.90\t13\t11\t88.4\t76.2
Tropical\tBase\t12.1\t0.56\t22\t18\t54.8\t42.7
Matcha\tBase\t3.8\t0.18\t21\t18\t18.0\t14.2
Ube\tBase\t4.2\t0.19\t23\t19\t18.3\t14.1
Pog\tBase\t3.9\t0.18\t21\t18\t17.8\t13.9
Blade\tBase\t4.0\t0.50\t8\t7\t49.0\t45.0
Honey Almond\tGranola\t4.1\t0.10\t42\t36\t9.6\t5.5
GF Maple Hemp-Flax\tGranola\t6.8\t0.71\t10\t8\t69.7\t62.9
Choco-Churro\tGranola\t3.3\t0.16\t21\t18\t15.4\t12.1
Roasted Coffee\tGranola\t3.7\t0.10\t38\t32\t9.6\t5.9
\t\t\t\t\t\t\t
\t\t\t\t\t\t\t
NOTES\t\t\t\t\t\t\t
\u2022 Stock units = packs/bags. "23.6" means 23 full packs + one at 60%.\t\t\t\t\t\t\t
\u2022 Avg Use/Day = net daily consumption from 22 days of ClickUp closing reports.\t\t\t\t\t\t\t
\u2022 Order Qty = how much to order to reach your buffer target.\t\t\t\t\t\t\t
\u2022 Change B5 (growth %) and B6 (buffer months) to adjust the forecast.\t\t\t\t\t\t\t
\u2022 Phase 2: Add recipe decomposition to sharpen per-ingredient consumption rates.\t\t\t\t\t\t\t`;

  // Press Enter/Escape first to ensure we're not in Name box edit mode
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);

  // Click on cell A1 area using keyboard shortcut
  await page.keyboard.press('Meta+Home');
  await page.waitForTimeout(500);

  // Write TSV to clipboard and paste
  await page.evaluate(async (data) => {
    await navigator.clipboard.writeText(data);
  }, tsvData);
  await page.waitForTimeout(300);

  // Paste
  await page.keyboard.press('Meta+v');
  await page.waitForTimeout(2000);

  return 'Paste completed';
}
