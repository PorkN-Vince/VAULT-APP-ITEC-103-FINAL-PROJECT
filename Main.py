import customtkinter as ctk
from PIL import Image, ImageTk
import os

ctk.set_appearance_mode("dark")


app = ctk.CTk()
app.geometry("900x600")
app.title("Vault Gallery")

BASE_DIR = "gallery_data"

# -------------------------
# TOP BAR
# -------------------------

top_frame = ctk.CTkFrame(app, fg_color="transparent")
top_frame.pack(fill="x", padx=20, pady=10)

title = ctk.CTkLabel(
    top_frame,
    text="Albums",
    font=("Arial", 28, "bold")
)
title.pack(side="left")

def create_album_popup():
    popup = ctk.CTkToplevel(app)
    popup.title("Create Album")
    popup.geometry("300x150")

    entry = ctk.CTkEntry(popup, placeholder_text="Album name")
    entry.pack(pady=20)

    def create():
        name = entry.get()
        if name:
            os.makedirs(os.path.join(BASE_DIR, name), exist_ok=True)
            load_albums()
            popup.destroy()

    btn = ctk.CTkButton(popup, text="Create", command=create)
    btn.pack()

# "+" Button
add_btn = ctk.CTkButton(
    top_frame,
    text="+",
    width=40,
    height=40,
    command=create_album_popup
)
add_btn.pack(side="right")

# -------------------------
# ALBUM GRID
# -------------------------

album_frame = ctk.CTkFrame(app)
album_frame.pack(fill="both", expand=True, padx=20)

def load_albums():
    for widget in album_frame.winfo_children():
        widget.destroy()

    row = 0
    col = 0

    folders = [f for f in os.listdir(BASE_DIR) if os.path.isdir(os.path.join(BASE_DIR, f))]

    for folder in folders:

        folder_path = os.path.join(BASE_DIR, folder)
        files = os.listdir(folder_path)

        # Album Card
        card = ctk.CTkFrame(album_frame, width=200, height=180)
        card.grid(row=row, column=col, padx=10, pady=10)

        # Thumbnail (first image)
        img_path = None
        for f in files:
            if f.lower().endswith((".png",".jpg",".jpeg")):
                img_path = os.path.join(folder_path, f)
                break

        if img_path:
            img = Image.open(img_path)
            img.thumbnail((180,120))
            photo = ImageTk.PhotoImage(img)

            img_label = ctk.CTkLabel(card, image=photo, text="")
            img_label.image = photo
            img_label.pack()

        else:
            placeholder = ctk.CTkLabel(card, text="No Image")
            placeholder.pack(expand=True)

        # Album Name
        name_label = ctk.CTkLabel(card, text=folder)
        name_label.pack()

        # Count
        count_label = ctk.CTkLabel(card, text=f"{len(files)} items", text_color="gray")
        count_label.pack()

        col += 1
        if col > 3:
            col = 0
            row += 1

load_albums()

app.mainloop()