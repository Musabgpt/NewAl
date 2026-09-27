---
name: تحليل البيانات وExcel
description: قراءة ملفات Excel/CSV وتحليلها وعمل جداول ورسوم بـ Python
triggers: excel, اكسل, إكسل, csv, جدول, بيانات, تحليل, رسم بياني, chart, xlsx, pandas, إحصائيات, احصائيات, متوسط
---
- Use Python with pandas: `pd.read_excel(path)` (needs openpyxl) or `pd.read_csv(path)`; print `df.shape`, `df.head()`, `df.describe()` first.
- Clean before analysing: strip column names, parse dates with `pd.to_datetime`, drop empty rows.
- Answer with numbers the code printed, never estimates. Round to 2 decimals.
- Charts: matplotlib, `plt.savefig('chart.png', dpi=120, bbox_inches='tight')` in the workspace, then report the path.
- Results for the user go to a new Excel file (`df.to_excel('result.xlsx', index=False)`), never overwrite their original.
- If pandas/openpyxl are missing, the code task installs them automatically.
