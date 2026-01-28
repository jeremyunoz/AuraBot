#!/usr/bin/env python3
"""
Setup script for Pi5 - Downloads and converts YOLO model to NCNN format.
Run this once on your Pi5 after cloning the repository.
"""

from ultralytics import YOLO
import os
import sys


def setup_model():
    """Download YOLO model and convert to NCNN format for Pi5."""
    model_name = "yolo26n.pt"  # Using YOLOv26 nano (latest version)
    ncnn_model_dir = "yolo26n_ncnn_model"
    
    print("Setting up YOLO model for Pi5...")
    print(f"Model: {model_name}")
    print(f"Output: {ncnn_model_dir}")
    print()
    
    # Check if NCNN model already exists
    if os.path.exists(ncnn_model_dir):
        print(f"✓ NCNN model already exists at {ncnn_model_dir}")
        response = input("Do you want to re-download and convert? (y/N): ")
        if response.lower() != 'y':
            print("Skipping model setup.")
            return
    
    try:
        # Download PyTorch model (will auto-download if not present)
        print(f"Downloading {model_name}...")
        model = YOLO(model_name)
        print(f"✓ Downloaded {model_name}")
        
        # Convert to NCNN format (optimized for Pi5)
        print(f"Converting to NCNN format...")
        model.export(format="ncnn")
        print(f"✓ Converted to {ncnn_model_dir}")
        
        print("\n✓ Model setup complete!")
        print(f"You can now use: model = YOLO('{ncnn_model_dir}', task='detect')")
        
    except Exception as e:
        print(f"\n✗ Error during setup: {e}")
        print("\nMake sure you have:")
        print("  - Internet connection (for downloading model)")
        print("  - ultralytics installed: pip install ultralytics")
        sys.exit(1)


if __name__ == "__main__":
    setup_model()
