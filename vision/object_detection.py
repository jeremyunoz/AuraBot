from ultralytics import YOLO
import cv2
import os


def check_person_detection(result):
    """
    Check if a person is detected in the YOLO results.
    
    Returns:
        dict: {
            'detected': bool,
            'count': int,
            'boxes': list of dicts with 'box' and 'confidence'
        }
    """
    person_info = {
        'detected': False,
        'count': 0,
        'boxes': []
    }
    
    if result.boxes is not None and len(result.boxes) > 0:
        class_ids = result.boxes.cls.cpu().numpy()
        class_names = result.names
        
        for i, class_id in enumerate(class_ids):
            if class_names[int(class_id)] == 'person':
                person_info['detected'] = True
                person_info['count'] += 1
                
                # Get bounding box and confidence
                box = result.boxes.xyxy[i].cpu().numpy()  # [x1, y1, x2, y2]
                confidence = float(result.boxes.conf[i].cpu().numpy())
                
                person_info['boxes'].append({
                    'box': box,
                    'confidence': confidence
                })
    
    return person_info


def main():
    # Model configuration - change these if needed
    MODEL_NAME = "yolo26n_ncnn_model"  # NCNN format for Pi5 (faster)
    FALLBACK_MODEL = "yolo26n.pt"  # PyTorch format (will auto-download)
    
    if os.path.exists(MODEL_NAME):
        print(f"Loading NCNN model: {MODEL_NAME}")
        model = YOLO(MODEL_NAME, task="detect")
    elif os.path.exists(FALLBACK_MODEL):
        print(f"Loading PyTorch model: {FALLBACK_MODEL}")
        model = YOLO(FALLBACK_MODEL)
    else:
        print(f"Model not found. Using default (will auto-download): {FALLBACK_MODEL}")
        print("For better Pi5 performance, run: python setup_model.py")
        model = YOLO(FALLBACK_MODEL)  # Will auto-download
    
    # Initialize webcam (0 is usually the default camera)
    cap = cv2.VideoCapture(0)
    
    if not cap.isOpened():
        print("Error: Could not open webcam")
        return
    
    print("Starting real-time object detection. Press 'q' to quit.")
    
    while True:
        # Read frame from webcam
        ret, frame = cap.read()
        
        if not ret:
            print("Error: Could not read frame")
            break
        
        # Run YOLO detection on the frame
        results = model(frame, verbose=False)
        
        # Access the first result (since we're processing one frame)
        result = results[0]
        
        # Check for person detection using the helper function
        person_info = check_person_detection(result)
        
        # Use the results
        if person_info['detected']:
            print(f"Person detected! Count: {person_info['count']}")
            for idx, person in enumerate(person_info['boxes'], 1):
                box = person['box']
                conf = person['confidence']
                print(f"  Person {idx}: confidence={conf:.2f}, bbox=[{box[0]:.0f}, {box[1]:.0f}, {box[2]:.0f}, {box[3]:.0f}]")
        else:
            print("No person detected")
        
        # Draw results on the frame
        annotated_frame = result.plot()
        
        # Display the frame
        cv2.imshow('Object Detection', annotated_frame)
        
        # Break loop on 'q' key press
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    
    # Cleanup
    cap.release()
    cv2.destroyAllWindows()
    print("Detection stopped.")


if __name__ == "__main__":
    main()

