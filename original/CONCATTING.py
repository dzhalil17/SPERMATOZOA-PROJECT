import pandas as pd

FILE_LIST = [r"D:\Lab\20 videos\Without grid\FILTERED DATA\3 4 5 new video\2v link_g 30, link_sp_diff 0.85, link_m_d 100, thresh 160, col_dist 30, min_dur 20\699-1139proc.xlsx",
            r"D:\Lab\20 videos\Without grid\FILTERED DATA\3 4 5 new video\2v link_g 30, link_sp_diff 0.85, link_m_d 100, thresh 160, col_dist 30, min_dur 20\1209-1596proc.xlsx",
            r"D:\Lab\20 videos\Without grid\FILTERED DATA\3 4 5 new video\2v link_g 30, link_sp_diff 0.85, link_m_d 100, thresh 160, col_dist 30, min_dur 20\1694-2065proc.xlsx",
            r"D:\Lab\20 videos\Without grid\FILTERED DATA\3 4 5 new video\2v link_g 30, link_sp_diff 0.85, link_m_d 100, thresh 160, col_dist 30, min_dur 20\2157-2640proc.xlsx",
            r"D:\Lab\20 videos\Without grid\FILTERED DATA\3 4 5 new video\2v link_g 30, link_sp_diff 0.85, link_m_d 100, thresh 160, col_dist 30, min_dur 20\2740-3144proc.xlsx",
            r"D:\Lab\20 videos\Without grid\FILTERED DATA\3 4 5 new video\2v link_g 30, link_sp_diff 0.85, link_m_d 100, thresh 160, col_dist 30, min_dur 20\3230-3650proc.xlsx",
            r"D:\Lab\20 videos\Without grid\FILTERED DATA\3 4 5 new video\2v link_g 30, link_sp_diff 0.85, link_m_d 100, thresh 160, col_dist 30, min_dur 20\3720-4125proc.xlsx",
            r"D:\Lab\20 videos\Without grid\FILTERED DATA\3 4 5 new video\2v link_g 30, link_sp_diff 0.85, link_m_d 100, thresh 160, col_dist 30, min_dur 20\4218-4618proc.xlsx",
            r"D:\Lab\20 videos\Without grid\FILTERED DATA\3 4 5 new video\2v link_g 30, link_sp_diff 0.85, link_m_d 100, thresh 160, col_dist 30, min_dur 20\4705-5141proc.xlsx",
            r"D:\Lab\20 videos\Without grid\FILTERED DATA\3 4 5 new video\2v link_g 30, link_sp_diff 0.85, link_m_d 100, thresh 160, col_dist 30, min_dur 20\5214-5615proc.xlsx",
            r"D:\Lab\20 videos\Without grid\FILTERED DATA\3 4 5 new video\2v link_g 30, link_sp_diff 0.85, link_m_d 100, thresh 160, col_dist 30, min_dur 20\327-626proc.xlsx",
            ]

OUTPUT_FILE = r"D:\Lab\20 videos\Without grid\CONCATENATED TABLES\2vnew link_g 30, link_sp_diff 0.85, link_m_d 100, thresh 160, col_dist 30, min_dur 20.xlsx"

# -------------------------------------------------

tracks_frames = []
summary_frames = []

for path in FILE_LIST:
    print(f"Читаю: {path}")
    xl = pd.ExcelFile(path)
    tracks_frames.append(pd.read_excel(xl,0))

merged_tracks = pd.concat(tracks_frames, ignore_index=True)

with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
    merged_tracks.to_excel(writer, sheet_name="Tracks", index=False)

print(f"\n✅ Готово! {len(tracks_frames)} файлов → {len(merged_tracks)} строк.")
print(f"   Сохранено в: {OUTPUT_FILE}")