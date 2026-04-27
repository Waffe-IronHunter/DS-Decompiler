import os
import zipfile
import subprocess
import shutil
import math
import struct
import traceback
import re
import xml.etree.ElementTree as ET
from xml.dom import minidom
from PIL import Image

# ==========================================
# DEBUG TOGGLE
# ==========================================
SKIP_TEXTURES = False

# ==========================================
# 1. KLEI HASHING & PERSISTENT DICTIONARY
# ==========================================
def klei_hash(text):
    hash_val = 0
    for char in text.lower():
        hash_val = (hash_val * 65599 + ord(char)) & 0xFFFFFFFF
    return hash_val

class HashManager:
    def __init__(self, dict_path="hash_dict.txt"):
        self.dict_path = dict_path
        self.hash_to_string = {}
        self.string_to_hash = {}
        self.new_strings = set()
        self._load_dict()

    def _load_dict(self):
        if os.path.exists(self.dict_path):
            with open(self.dict_path, 'r', encoding='utf-8') as f:
                for line in f:
                    s = line.strip()
                    if s:
                        h = klei_hash(s)
                        self.hash_to_string[h] = s
                        self.string_to_hash[s] = h

    def save_dict(self):
        if self.new_strings:
            with open(self.dict_path, 'a', encoding='utf-8') as f:
                for s in sorted(self.new_strings):
                    f.write(s + '\n')
            self.new_strings.clear()

    def add_string(self, s):
        h = klei_hash(s)
        if h not in self.hash_to_string:
            self.hash_to_string[h] = s
            self.string_to_hash[s] = h
            self.new_strings.add(s)

    def harvest_strings_from_file(self, filepath):
        with open(filepath, 'rb') as f:
            data = f.read()
            
        matches = re.findall(b'[a-zA-Z_][a-zA-Z0-9_-]{2,}', data)
        for match in matches:
            try:
                s = match.decode('ascii')
                self.add_string(s)
            except:
                pass

    def get_string(self, hash_val):
        return self.hash_to_string.get(hash_val, f"hash_{hash_val}")

    def get_hash(self, string_val):
        return self.string_to_hash.get(string_val, klei_hash(string_val))

# ==========================================
# 2. EXACT KTOOLS BUILD PARSER
# ==========================================
def parse_build_file(filepath):
    with open(filepath, "rb") as f:
        magic = f.read(4)
        if magic != b"BILD":
            raise ValueError("Not a valid BILD file")

        version = struct.unpack("<I", f.read(4))[0]
        numsymbols, numframes = struct.unpack("<II", f.read(8))

        name_len = struct.unpack("<I", f.read(4))[0]
        build_name = f.read(name_len).decode('ascii', errors='ignore')

        numatlases = struct.unpack("<I", f.read(4))[0]
        atlases =[]
        for _ in range(numatlases):
            alen = struct.unpack("<I", f.read(4))[0]
            atlases.append(f.read(alen).decode('ascii', errors='ignore'))

        symbols =[]
        for _ in range(numsymbols):
            sym_hash = struct.unpack("<I", f.read(4))[0]
            sym_numframes = struct.unpack("<I", f.read(4))[0]

            frames =[]
            for _ in range(sym_numframes):
                framenum, duration = struct.unpack("<II", f.read(8))
                bbox_x, bbox_y, w, h = struct.unpack("<ffff", f.read(16))
                alphaidx, alphacount = struct.unpack("<II", f.read(8))
                frames.append({
                    "framenum": framenum,
                    "duration": duration,
                    "bbox_x": bbox_x,
                    "bbox_y": bbox_y,
                    "w": w,
                    "h": h,
                    "alphacount": alphacount
                })
            symbols.append({
                "symbol_hash": sym_hash,
                "frames": frames
            })

        alphaverts = struct.unpack("<I", f.read(4))[0]

        min_sampler = float('inf')
        for sym in symbols:
            for frame in sym["frames"]:
                numtrigs = frame["alphacount"] // 3
                min_u, min_v = float('inf'), float('inf')
                max_u, max_v = float('-inf'), float('-inf')
                
                min_x, min_y = float('inf'), float('inf')
                max_x, max_y = float('-inf'), float('-inf')
                
                sampler_w = 0.0

                for _ in range(numtrigs):
                    for _ in range(3):
                        _x, _y, _z = struct.unpack("<fff", f.read(12))
                        _u, _v, _w = struct.unpack("<fff", f.read(12))
                        
                        min_x = min(min_x, _x)
                        min_y = min(min_y, _y)
                        max_x = max(max_x, _x)
                        max_y = max(max_y, _y)
                        
                        min_u = min(min_u, _u)
                        min_v = min(min_v, _v)
                        max_u = max(max_u, _u)
                        max_v = max(max_v, _v)
                        sampler_w = _w

                if numtrigs == 0:
                    min_u = min_v = max_u = max_v = 0.0
                    min_x = min_y = max_x = max_y = 0.0

                frame["min_u"] = max(0.0, min(1.0, min_u))
                frame["min_v"] = max(0.0, min(1.0, min_v))
                frame["max_u"] = max(0.0, min(1.0, max_u))
                frame["max_v"] = max(0.0, min(1.0, max_v))
                
                frame["min_x"] = min_x
                frame["min_y"] = min_y
                frame["max_x"] = max_x
                frame["max_y"] = max_y
                
                frame["sampler"] = int(round(sampler_w))
                min_sampler = min(min_sampler, frame["sampler"])

        if min_sampler == float('inf'):
            min_sampler = 0

        for sym in symbols:
            for frame in sym["frames"]:
                frame["atlas_idx"] = frame["sampler"] - min_sampler

        return build_name, symbols, atlases

# ==========================================
# 3. EXACT KTOOLS ANIM PARSER
# ==========================================
FACING_MAP = {
    1: "_right", 2: "_up", 4: "_left", 8: "_down",
    16: "_upright", 32: "_upleft", 64: "_downright", 128: "_downleft",
    5: "_side", 48: "_upside", 192: "_downside", 240: "_45s", 15: "_90s"
}

def parse_anim_file(filepath):
    with open(filepath, "rb") as f:
        magic = f.read(4)
        if magic != b"ANIM":
            raise ValueError("Not a valid ANIM file")

        version = struct.unpack("<I", f.read(4))[0]
        numelements, numframes, numevents, numanims = struct.unpack("<IIII", f.read(16))

        anims =[]
        for _ in range(numanims):
            name_len = struct.unpack("<I", f.read(4))[0]
            name = f.read(name_len).decode('ascii', errors='ignore')

            facing_byte = struct.unpack("<B", f.read(1))[0]
            bank_hash, frame_rate, anim_numframes = struct.unpack("<IfI", f.read(12))

            frames =[]
            for _ in range(anim_numframes):
                x, y, w, h, frame_numevents = struct.unpack("<ffffI", f.read(20))

                events =[]
                for _ in range(frame_numevents):
                    ev_hash = struct.unpack("<I", f.read(4))[0]
                    events.append(ev_hash)

                frame_numelements = struct.unpack("<I", f.read(4))[0]
                elements =[]
                for _ in range(frame_numelements):
                    sym_hash, build_frame, layername_hash, a, b, c, d, tx, ty, z = struct.unpack("<IIIfffffff", f.read(40))
                    elements.append({
                        "symbol_hash": sym_hash,
                        "build_frame": build_frame,
                        "layername_hash": layername_hash,
                        "a": a, "b": b, "c": c, "d": d, "tx": tx, "ty": ty, "z_index": z
                    })

                frames.append({
                    "x": x, "y": y, "w": w, "h": h,
                    "events": events,
                    "elements": elements
                })

            full_name = name + FACING_MAP.get(facing_byte, "")

            anims.append({
                "name": full_name,
                "facing_byte": facing_byte,
                "bank_hash": bank_hash,
                "frame_rate": frame_rate,
                "num_frames": anim_numframes,
                "frames": frames
            })

        return anims

# ==========================================
# 4. GLOBAL BUILD REGISTRY & OVERRIDES
# ==========================================
class BuildRegistry:
    def __init__(self, hash_manager):
        self.symbols = {} 
        self.builds = {} 
        self.overrides = {} 
        self.hashes = hash_manager

    def add_build_data(self, build_name, symbols, atlas_png_paths):
        self.builds[build_name] = {'atlases': atlas_png_paths}
        
        for symbol in symbols:
            sym_hash = symbol['symbol_hash']
            if sym_hash not in self.symbols:
                self.symbols[sym_hash] = {}
                
            if not symbol['frames']:
                continue
                
            max_framenum = max(f['framenum'] for f in symbol['frames'])
            if max_framenum > 1000: max_framenum = 1000 
            
            existing_frames = {f['framenum']: f for f in symbol['frames']}
            frame_list = []
            last_valid_frame = symbol['frames'][0]
            
            for i in range(max_framenum + 1):
                if i in existing_frames:
                    f = existing_frames[i]
                    last_valid_frame = f
                    frame_list.append({
                        'build_name': build_name,
                        'symbol_hash': sym_hash,
                        'framenum': i,
                        'image_framenum': i, # Points to its own image
                        'bbox_x': f['bbox_x'],
                        'bbox_y': f['bbox_y'],
                        'w': f['w'],
                        'h': f['h'],
                        'min_u': f['min_u'],
                        'min_v': f['min_v'],
                        'max_u': f['max_u'],
                        'max_v': f['max_v'],
                        'min_x': f['min_x'],
                        'min_y': f['min_y'],
                        'max_x': f['max_x'],
                        'max_y': f['max_y'],
                        'atlas_idx': f['atlas_idx'],
                        'is_blank': False
                    })
                else:
                    # Missing frame! Inherit everything from the last valid frame, and point to its image!
                    frame_list.append({
                        'build_name': build_name,
                        'symbol_hash': sym_hash,
                        'framenum': i,
                        'image_framenum': last_valid_frame['framenum'], # Points to the previous valid image!
                        'bbox_x': last_valid_frame['bbox_x'],
                        'bbox_y': last_valid_frame['bbox_y'],
                        'w': last_valid_frame['w'],
                        'h': last_valid_frame['h'],
                        'min_u': 0, 'min_v': 0, 'max_u': 0, 'max_v': 0,
                        'min_x': 0, 'min_y': 0, 'max_x': 0, 'max_y': 0,
                        'atlas_idx': 0,
                        'is_blank': True
                    })
                    
            self.symbols[sym_hash][build_name] = frame_list

    def resolve_symbol_pointer(self, symbol_hash):
        if symbol_hash in self.overrides:
            return self.overrides[symbol_hash]
            
        build_dict = self.symbols.get(symbol_hash, {})
        if not build_dict:
            return None, symbol_hash
            
        b_name = list(build_dict.keys())[0]
        return b_name, symbol_hash

# ==========================================
# 5. MATH: MATRIX TO SPRITER TRANSFORMS
# ==========================================
def decompose_matrix(a, b, c, d, tx, ty, last_scale_x, last_scale_y, last_angle, is_first):
    scale_x = math.sqrt(a*a + b*b)
    scale_y = math.sqrt(c*c + d*d)
    det = a*d - c*b
    
    if det < 0:
        if is_first or last_scale_x <= last_scale_y:
            scale_x = -scale_x
            is_first = False
        else:
            scale_y = -scale_y
            
    if abs(scale_x) < 1e-3 or abs(scale_y) < 1e-3:
        angle_rad = last_angle
    else:
        sin_approx = 0.5 * (c / scale_y - b / scale_x)
        cos_approx = 0.5 * (a / scale_x + d / scale_y)
        angle_rad = math.atan2(sin_approx, cos_approx)
        
    spin = 1 if abs(angle_rad - last_angle) <= math.pi else -1
    if angle_rad < last_angle:
        spin = -spin
        
    if angle_rad < 0:
        angle_rad += 2 * math.pi
        
    angle_deg = math.degrees(angle_rad)
    
    return {
        "x": tx, "y": -ty, 
        "angle": angle_deg, 
        "scale_x": scale_x, "scale_y": scale_y,
        "rad": angle_rad,
        "spin": spin,
        "is_first": is_first
    }

class Timeline:
    def __init__(self, id, name, layer_hash, symbol_hash):
        self.id = id
        self.name = name
        self.layer_hash = layer_hash
        self.symbol_hash = symbol_hash
        self.keys = []
        self.last_scale_x = 1.0
        self.last_scale_y = 1.0
        self.last_angle = 0.0
        self.is_first = True

# ==========================================
# 6. CONSOLIDATED SCML GENERATOR & CROPPER
# ==========================================
class SCMLBuilder:
    def __init__(self, decompiler, output_dir):
        self.decompiler = decompiler
        self.output_dir = output_dir
        self.hashes = decompiler.hashes
        self.registry = decompiler.registry
        self.folders = {}
        self.files = {}
        self.missing_id = None

    def build_consolidated_scml(self, list_of_anims, output_path):
        print(f"\nGenerating Consolidated SCML and Cropping Textures...", flush=True)
        root = ET.Element("spriter_data", scml_version="1.0", generator="KleiDecompiler", generator_version="29.0")
        
        self._build_folders_and_files(root, list_of_anims)
        
        entities = {}
        for anims in list_of_anims:
            for anim_record in anims:
                bank_hash = anim_record['bank_hash']
                if bank_hash not in entities:
                    entities[bank_hash] = []
                entities[bank_hash].append(anim_record)
                
        entity_id = 0
        for bank_hash, anim_records in entities.items():
            bank_name = self.hashes.get_string(bank_hash)
            entity = ET.SubElement(root, "entity", id=str(entity_id), name=bank_name)
            
            anim_idx = 0
            for anim_record in anim_records:
                self._build_animation(entity, anim_idx, anim_record)
                anim_idx += 1
            entity_id += 1
                
        xml_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent="    ")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(xml_str)

    def _build_folders_and_files(self, root, list_of_anims):
        folder_count = 0
        missing_folder = ET.SubElement(root, "folder", id=str(folder_count), name="MISSING_DATA")
        ET.SubElement(missing_folder, "file", id="0", name="MISSING_SYMBOL.png", width="10", height="10", pivot_x="0.5", pivot_y="0.5")
        self.missing_id = folder_count
        folder_count += 1

        unique_symbols = set()
        for anims in list_of_anims:
            for anim in anims:
                for frame in anim['frames']:
                    for el in frame['elements']:
                        unique_symbols.add(el['symbol_hash'])

        opened_atlases = {}

        for sym_hash in unique_symbols:
            sym_name = self.hashes.get_string(sym_hash)
            b_name, actual_sym_hash = self.registry.resolve_symbol_pointer(sym_hash)
            
            if not b_name:
                self.files[sym_hash] = {0: {'folder_id': self.missing_id, 'file_id': 0, 'name': sym_name}}
                continue
                
            frame_list = self.registry.symbols[actual_sym_hash][b_name]
            atlas_paths = self.registry.builds[b_name]['atlases']
            
            folder_id = folder_count
            folder_count += 1
            ET.SubElement(root, "folder", id=str(folder_id), name=sym_name)
            
            os_folder = os.path.join(self.output_dir, sym_name)
            os.makedirs(os_folder, exist_ok=True)
            
            self.files[sym_hash] = {}
            
            file_id = 0
            for frame_data in frame_list:
                framenum = frame_data['framenum']
                image_framenum = frame_data['image_framenum']
                
                frame_name = f"{sym_name}-{framenum}"
                image_name = f"{sym_name}-{image_framenum}"
                file_path_scml = f"{sym_name}/{image_name}.png"
                    
                self.files[sym_hash][framenum] = {
                    'folder_id': folder_id, 
                    'file_id': file_id, 
                    'name': frame_name
                }
                
                w_ceil = math.ceil(frame_data['w'])
                h_ceil = math.ceil(frame_data['h'])
                if w_ceil <= 0: w_ceil = 1
                if h_ceil <= 0: h_ceil = 1
                
                px = 0.5 - (frame_data['bbox_x'] / w_ceil)
                py = 0.5 + (frame_data['bbox_y'] / h_ceil)
                
                # Only crop and save if this is an actual unique frame (not a blank/duplicate)
                if not frame_data['is_blank']:
                    atlas_idx = frame_data['atlas_idx']
                    if atlas_idx < len(atlas_paths):
                        atlas_path = atlas_paths[atlas_idx]
                        if atlas_path not in opened_atlases:
                            try:
                                img = Image.open(atlas_path)
                                img.load()
                                opened_atlases[atlas_path] = img
                            except:
                                opened_atlases[atlas_path] = None
                                
                        atlas_img = opened_atlases[atlas_path]
                        if atlas_img:
                            aw, ah = atlas_img.size
                            min_v_inv = 1.0 - frame_data['max_v']
                            max_v_inv = 1.0 - frame_data['min_v']
                            
                            left = math.floor(frame_data['min_u'] * aw)
                            upper = math.floor(min_v_inv * ah)
                            right = math.ceil(frame_data['max_u'] * aw)
                            lower = math.ceil(max_v_inv * ah)
                            
                            crop_width = right - left
                            crop_height = lower - upper
                            
                            true_crop_w = int(round(frame_data['max_x'] - frame_data['min_x']))
                            true_crop_h = int(round(frame_data['max_y'] - frame_data['min_y']))
                            
                            if crop_width > 0 and crop_height > 0 and true_crop_w > 0 and true_crop_h > 0:
                                try:
                                    crop_img = atlas_img.crop((left, upper, right, lower))
                                    
                                    if crop_img.size != (true_crop_w, true_crop_h):
                                        crop_img = crop_img.resize((true_crop_w, true_crop_h), Image.Resampling.LANCZOS)
                                        
                                    final_img = Image.new('RGBA', (w_ceil, h_ceil), (0, 0, 0, 0))
                                    
                                    paste_x = int(round(frame_data['min_x'] - frame_data['bbox_x'] + frame_data['w'] / 2.0))
                                    paste_y = int(round(frame_data['bbox_y'] + frame_data['h'] / 2.0 - frame_data['max_y']))
                                    
                                    final_img.paste(crop_img, (paste_x, paste_y))
                                    final_img.save(os.path.join(os_folder, f"{frame_name}.png"))
                                except Exception as e:
                                    print(f"      ->[ERROR] Failed to crop {frame_name}: {e}", flush=True)

                folder_elem = root.find(f".//folder[@id='{folder_id}']")
                ET.SubElement(folder_elem, "file", id=str(file_id), name=file_path_scml,
                              width=str(w_ceil), height=str(h_ceil),
                              pivot_x=str(px), pivot_y=str(py))
                file_id += 1

    def _build_animation(self, entity, anim_idx, anim_record):
        anim_name = anim_record['name']
        frame_rate = anim_record['frame_rate']
        num_frames = anim_record['num_frames']
        
        length_ms = int(round((num_frames * 1000.0) / frame_rate)) if frame_rate > 0 else num_frames * 33
        interval = int(1000 / frame_rate) if frame_rate > 0 else 33
        
        animation = ET.SubElement(entity, "animation", id=str(anim_idx), name=anim_name, length=str(length_ms), interval=str(interval))
        mainline = ET.SubElement(animation, "mainline")
        
        timelines = []
        
        for frame_offset in range(num_frames + 1):
            if num_frames == 0:
                break
                
            if frame_offset < num_frames:
                frame = anim_record['frames'][frame_offset]
                time_ms = int(round((frame_offset * 1000.0) / frame_rate)) if frame_rate > 0 else frame_offset * 33
            else:
                frame = anim_record['frames'][-1]
                time_ms = length_ms
                
            mainline_key = ET.SubElement(mainline, "key", id=str(frame_offset), time=str(time_ms))
            
            active_tls_this_frame = set()
            
            for i, element in enumerate(frame['elements']):
                layer_hash = element['layername_hash']
                sym_hash = element['symbol_hash']
                
                tl = None
                for t in timelines:
                    if t.layer_hash == layer_hash and t.symbol_hash == sym_hash and t.id not in active_tls_this_frame:
                        tl = t
                        break
                        
                if not tl:
                    layer_name = self.hashes.get_string(layer_hash)
                    count = sum(1 for t in timelines if t.layer_hash == layer_hash)
                    tl_name = layer_name if count == 0 else f"{layer_name}_{count}"
                    tl = Timeline(len(timelines), tl_name, layer_hash, sym_hash)
                    timelines.append(tl)
                    
                active_tls_this_frame.add(tl.id)
                
                z_index = len(frame['elements']) - i - 1
                
                tl_key_id = len(tl.keys)
                ET.SubElement(mainline_key, "object_ref", id=str(i), timeline=str(tl.id), key=str(tl_key_id), z_index=str(z_index))
                
                trans = decompose_matrix(
                    element['a'], element['b'], element['c'], element['d'], 
                    element['tx'], element['ty'], 
                    tl.last_scale_x, tl.last_scale_y, tl.last_angle, tl.is_first
                )
                
                tl.last_scale_x = trans['scale_x']
                tl.last_scale_y = trans['scale_y']
                tl.last_angle = trans['rad']
                tl.is_first = trans['is_first']
                
                file_dict = self.files.get(element['symbol_hash'], {})
                build_frame = element['build_frame']
                file_info = None
                
                if file_dict:
                    valid_frames = [f for f in file_dict.keys() if f <= build_frame]
                    if valid_frames:
                        file_info = file_dict[max(valid_frames)]
                    else:
                        file_info = file_dict[min(file_dict.keys())]
                else:
                    file_info = {'folder_id': self.missing_id, 'file_id': 0}
                
                tl.keys.append({
                    'id': tl_key_id, 'time': time_ms, 'folder': file_info['folder_id'], 'file': file_info['file_id'],
                    'x': trans['x'], 'y': trans['y'], 'angle': trans['angle'],
                    'scale_x': trans['scale_x'], 'scale_y': trans['scale_y'],
                    'spin': trans['spin']
                })

        for tl in timelines:
            timeline = ET.SubElement(animation, "timeline", id=str(tl.id), name=tl.name)
            for k in tl.keys:
                key = ET.SubElement(timeline, "key", id=str(k['id']), time=str(k['time']), spin=str(k['spin']), curve_type="instant")
                ET.SubElement(key, "object", folder=str(k['folder']), file=str(k['file']),
                              x=str(k['x']), y=str(k['y']), angle=str(k['angle']),
                              scale_x=str(k['scale_x']), scale_y=str(k['scale_y']))

# ==========================================
# 7. TEXTURE MANAGER
# ==========================================
class TextureManager:
    def __init__(self, decompiler, ktech_path="ktools/ktech.exe"):
        self.decompiler = decompiler
        self.ktech_path = os.path.abspath(ktech_path)

    def convert_tex_to_png(self, tex_filepath):
        abs_tex = os.path.abspath(tex_filepath)
        tex_dir = os.path.dirname(abs_tex)
        tex_base = os.path.basename(abs_tex)
        png_base = tex_base.replace(".tex", ".png")
        abs_png = os.path.join(tex_dir, png_base)
        
        print(f"    -> Running ktech on {tex_base}...", flush=True)
        try:
            creation_flags = 0x08000000 if os.name == 'nt' else 0
            try:
                subprocess.run([self.ktech_path, abs_tex, abs_png], 
                    cwd=tex_dir,
                    check=True, 
                    stdout=subprocess.DEVNULL, 
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    creationflags=creation_flags
                )
            except subprocess.CalledProcessError:
                subprocess.run([self.ktech_path, abs_tex], 
                    cwd=tex_dir,
                    check=True, 
                    stdout=subprocess.DEVNULL, 
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    creationflags=creation_flags
                )
            
            cwd_png = os.path.abspath(png_base)
            if not os.path.exists(abs_png) and os.path.exists(cwd_png):
                shutil.move(cwd_png, abs_png)
                
            if os.path.exists(abs_png):
                return abs_png
            else:
                print(f"    -> [ERROR] ktech finished, but {png_base} was not found!", flush=True)
                return None
                
        except Exception as e:
            print(f"    -> [ERROR] ktech failed: {e}", flush=True)
            return None

# ==========================================
# 8. CORE DECOMPILER & AUTO-PIPELINE
# ==========================================
class KleiDecompiler:
    def __init__(self):
        self.hashes = HashManager()
        self.registry = BuildRegistry(self.hashes)

class KleiPipeline:
    def __init__(self, decompiler):
        self.decompiler = decompiler
        self.tex_manager = TextureManager(decompiler)

    def process_all_zips(self, output_dir="./decompiled_project"):
        zips =[f for f in os.listdir('.') if f.endswith('.zip')]
        if not zips:
            print("No .zip files found in the current directory. Please place them next to this script.", flush=True)
            return

        print(f"Found {len(zips)} zip files: {', '.join(zips)}", flush=True)
        temp_dir = os.path.join(output_dir, "_temp")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir, exist_ok=True)

        for zip_file in zips:
            zip_name = os.path.splitext(zip_file)[0]
            extract_path = os.path.join(temp_dir, zip_name)
            os.makedirs(extract_path, exist_ok=True)
            with zipfile.ZipFile(zip_file, 'r') as zip_ref:
                zip_ref.extractall(extract_path)

        print("\n--- Harvesting Strings ---", flush=True)
        for root, _, files in os.walk(temp_dir):
            for file in files:
                if file.endswith((".build", ".anim", ".bin")):
                    self.decompiler.hashes.harvest_strings_from_file(os.path.join(root, file))
        self.decompiler.hashes.save_dict()

        print("\n--- Processing Builds & Textures ---", flush=True)
        for root, _, files in os.walk(temp_dir):
            for file in files:
                if file.endswith(".build") or file == "build.bin":
                    build_path = os.path.join(root, file)
                    build_name = os.path.basename(root) if file == "build.bin" else file.replace('.build', '')
                    
                    print(f"Loading build: {build_name}...", flush=True)
                    try:
                        build_name_internal, symbols, atlases = parse_build_file(build_path)
                        print(f"  -> Parsed successfully! ({len(symbols)} symbols, {len(atlases)} atlases)", flush=True)
                        
                        atlas_png_paths = []
                        if not SKIP_TEXTURES:
                            for atlas_file in atlases:
                                tex_path = os.path.join(root, atlas_file)
                                if os.path.exists(tex_path):
                                    atlas_png = self.tex_manager.convert_tex_to_png(tex_path)
                                    if atlas_png:
                                        atlas_png_paths.append(atlas_png)
                                else:
                                    print(f"  -> [WARNING] Expected texture '{atlas_file}' not found in zip!", flush=True)
                        
                        self.decompiler.registry.add_build_data(build_name, symbols, atlas_png_paths)
                        
                    except Exception as e:
                        print(f"  -> [ERROR] Failed to parse {build_name}: {e}", flush=True)

        print("\n--- Parsing Animations ---", flush=True)
        parsed_anims =[]
        for root, _, files in os.walk(temp_dir):
            for file in files:
                if file.endswith(".anim") or file == "anim.bin":
                    anim_path = os.path.join(root, file)
                    anim_name = os.path.basename(root) if file == "anim.bin" else file.replace('.anim', '')
                    
                    print(f"Loading anim: {anim_name}...", flush=True)
                    try:
                        anims = parse_anim_file(anim_path)
                        parsed_anims.append(anims)
                        print(f"  -> Parsed successfully! ({len(anims)} animations)", flush=True)
                    except Exception as e:
                        print(f"  ->[ERROR] Failed to parse {anim_name}: {e}", flush=True)

        self._resolve_symbols(parsed_anims)

        if parsed_anims:
            scml_path = os.path.join(output_dir, "consolidated_project.scml")
            builder = SCMLBuilder(self.decompiler, output_dir)
            builder.build_consolidated_scml(parsed_anims, scml_path)

        missing_dir = os.path.join(output_dir, "MISSING_DATA")
        os.makedirs(missing_dir, exist_ok=True)
        Image.new('RGBA', (10, 10), (255, 0, 0, 0)).save(os.path.join(missing_dir, "MISSING_SYMBOL.png"))
        
        print(f"\n=== Finished! Everything saved to: {os.path.abspath(output_dir)} ===", flush=True)

    def _resolve_symbols(self, parsed_anims):
        required_hashes = set()
        for anims in parsed_anims:
            for anim in anims:
                for frame in anim['frames']:
                    for el in frame['elements']:
                        required_hashes.add(el['symbol_hash'])

        missing_hashes = []
        conflicting_hashes = []

        for h in required_hashes:
            builds_containing_hash = self.decompiler.registry.symbols.get(h, {})
            if len(builds_containing_hash) == 0:
                missing_hashes.append(h)
            elif len(builds_containing_hash) > 1:
                conflicting_hashes.append(h)

        if conflicting_hashes:
            print("\n" + "="*50, flush=True)
            print("ATTENTION: DUPLICATE SYMBOLS DETECTED", flush=True)
            print("="*50, flush=True)
            for h in conflicting_hashes:
                sym_name = self.decompiler.hashes.get_string(h)
                builds = list(self.decompiler.registry.symbols[h].keys())
                print(f"Symbol '{sym_name}' is present in multiple builds:")
                for idx, b in enumerate(builds):
                    print(f"  [{idx+1}] {b}")
                while True:
                    choice = input(f"Which build should provide '{sym_name}'? (1-{len(builds)}): ").strip()
                    if choice.isdigit() and 1 <= int(choice) <= len(builds):
                        selected_build = builds[int(choice)-1]
                        self.decompiler.registry.overrides[h] = (selected_build, h)
                        break
                    print("Invalid choice.")

        if missing_hashes:
            print("\n" + "="*50, flush=True)
            print("ATTENTION: MISSING SYMBOLS DETECTED", flush=True)
            print("="*50, flush=True)
            
            build_to_symbols = {}
            for sym_hash, build_dict in self.decompiler.registry.symbols.items():
                if not build_dict: continue
                b_name = list(build_dict.keys())[0]
                sym_name = self.decompiler.hashes.get_string(sym_hash)
                if b_name not in build_to_symbols:
                    build_to_symbols[b_name] = set()
                build_to_symbols[b_name].add(sym_name)
                
            print("Available symbols to use as replacements:")
            for b_name, syms in build_to_symbols.items():
                print(f"  [{b_name}]: {', '.join(sorted(syms))}")
            print("")

            for h in missing_hashes:
                sym_name = self.decompiler.hashes.get_string(h)
                print(f"Missing Symbol: '{sym_name}'", flush=True)
                substitute = input(f"Enter replacement (or press Enter to SKIP): ").strip()
                if substitute:
                    sub_hash = self.decompiler.hashes.get_hash(substitute)
                    sub_builds = list(self.decompiler.registry.symbols.get(sub_hash, {}).keys())
                    
                    if not sub_builds:
                        print(f"  -> Warning: '{substitute}' is also missing! Skipping.")
                    elif len(sub_builds) == 1:
                        self.decompiler.registry.overrides[h] = (sub_builds[0], sub_hash)
                        print(f"  -> Override Set: '{sym_name}' will use '{substitute}' from '{sub_builds[0]}'")
                    else:
                        print(f"  -> '{substitute}' is in multiple builds:")
                        for idx, b in enumerate(sub_builds):
                            print(f"    [{idx+1}] {b}")
                        while True:
                            choice = input(f"  -> Which build should provide '{substitute}'? (1-{len(sub_builds)}): ").strip()
                            if choice.isdigit() and 1 <= int(choice) <= len(sub_builds):
                                selected_build = sub_builds[int(choice)-1]
                                self.decompiler.registry.overrides[h] = (selected_build, sub_hash)
                                print(f"  -> Override Set: '{sym_name}' will use '{substitute}' from '{selected_build}'")
                                break

        print("\n" + "="*50, flush=True)
        print("MANUAL OVERRIDES", flush=True)
        print("="*50, flush=True)
        while True:
            ans = input("Do you want to manually override any other symbols? (y/N): ").strip().lower()
            if ans != 'y':
                break
            target = input("Enter the symbol you want to REPLACE (e.g. 'head'): ").strip()
            if not target: continue
            substitute = input(f"Enter the symbol to use INSTEAD of '{target}': ").strip()
            if not substitute: continue
            
            t_hash = self.decompiler.hashes.get_hash(target)
            s_hash = self.decompiler.hashes.get_hash(substitute)
            
            sub_builds = list(self.decompiler.registry.symbols.get(s_hash, {}).keys())
            if not sub_builds:
                print(f"  -> Warning: '{substitute}' is not in any loaded build! Override ignored.")
            elif len(sub_builds) == 1:
                self.decompiler.registry.overrides[t_hash] = (sub_builds[0], s_hash)
                print(f"  -> Override Set: '{target}' will use '{substitute}' from '{sub_builds[0]}'")
            else:
                print(f"  -> '{substitute}' is in multiple builds:")
                for idx, b in enumerate(sub_builds):
                    print(f"    [{idx+1}] {b}")
                while True:
                    choice = input(f"  -> Which build should provide '{substitute}'? (1-{len(sub_builds)}): ").strip()
                    if choice.isdigit() and 1 <= int(choice) <= len(sub_builds):
                        selected_build = sub_builds[int(choice)-1]
                        self.decompiler.registry.overrides[t_hash] = (selected_build, s_hash)
                        print(f"  -> Override Set: '{target}' will use '{substitute}' from '{selected_build}'")
                        break

# ==========================================
# 9. EXECUTION
# ==========================================
if __name__ == "__main__":
    try:
        print("Starting Klei Decompiler Pipeline...", flush=True)
        decompiler = KleiDecompiler()
        pipeline = KleiPipeline(decompiler)
        pipeline.process_all_zips()
    except Exception as e:
        print("\n" + "="*50, flush=True)
        print("CRASH DETECTED! Here is the error for the AI:", flush=True)
        print("="*50, flush=True)
        traceback.print_exc()
        print("="*50, flush=True)
    finally:
        input("\nPress Enter to exit...")
