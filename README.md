# DS Decompiler (Python)
Ktools ported to Python and improved

A modern, open-source Python decompiler for Klei Entertainment's `.anim`, `.build`, and `.tex` files. 
This tool converts compiled Don't Starve / Don't Starve Together animation files back into fully editable Spriter (`.scml`) projects.

## 🌟 Improvements over `ktools`
This project was built to replace `ktools` by fixing several long-standing mathematical and logical bugs:
* **Sub-Pixel Pivot Bug Fixed:** `ktools` stretched cropped textures to fit ceiled canvas sizes, causing compounding pivot offsets (misaligned torsos/heads). This tool perfectly restores the original transparent padding.
* **Grouped Symbol Bug Fixed:** `ktools` merged timelines with the same image hash. This tool groups by layer hash, perfectly preserving togglable states like `arm_carry` vs `arm_normal`.
* **Interpolation Snapping Fixed:** Automatically applies `curve_type="instant"` to all keyframes to prevent Spriter from tweening baked 30fps frames, eliminating 360-degree snapping.
* **Interactive Conflict Resolution:** If multiple builds contain the same symbol (e.g., `head`), the tool interactively asks you which build to use, preventing cross-contamination.
* **Support for multiple .zip:** `ktools` Can't decompile multiple anim.bin and build.bin at the same time, and consolidate them into one .scml file
* **Preserves missing symbols:** `ktools` Omits missing symbols in the .scml file, this code allows you to override missing symbols with another e.g. swap_object with swap_axe

## 📥 Installation
1. Install [Python 3.x](https://www.python.org/downloads/).
2. Install the required image library by opening your command prompt/terminal and running:
   `pip install Pillow`
3. Get ktech.exe: Because Klei's .tex files use highly optimized DXT compression, this script relies on ktech.exe to decode the raw textures. You must download ktech.exe (from the original ktools release) and place it in the exact same folder as ds_decompiler.py.
    (Note: If you prefer to keep ktech.exe elsewhere, you can edit the ktech_path variable inside the Python script to point to its absolute path).

## 🚀 Usage
1. Place your mod's `.zip` files (containing `.anim`, `.build`, and `.tex` files) into the same folder as `ds_decompiler.py`.
2. Run the script:
   `python ds_decompiler.py`
3. The script will automatically extract the files, harvest the strings, crop the textures, and generate a `decompiled_project` folder containing your ready-to-edit `.scml` file.

## 📖 The Hash Dictionary (`hash_dict.txt`)
Klei's engine converts symbol names (like `swap_object`) into 32-bit integer hashes. Because hashes cannot be reversed, this tool builds a dictionary of known words. 
Every time you run the tool, it scrapes the binary files for new strings and saves them to `hash_dict.txt`. You can manually add words to this file (one per line) if you encounter missing symbols.
I prepopulated the hash_dict using an automated script to grab all relevant strings from the scripts folder

## ⚖️ License & Credits
This software is released under the **GNU General Public License v2.0 (GPLv2)**.
* Core binary structure and matrix math reverse-engineered by **Simplex** (original author of `[ktools](https://github.com/nsimplex/ktools)`).
* Python port, UX, and mathematical bug fixes by Iron_Hunter for the Don't Starve Modding Community.
