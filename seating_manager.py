import numpy as np

class SeatingManager:
    def __init__(self, rows=7, cols=4):
        self.rows = rows
        self.cols = cols
        self.seat_mapping = {}  # maps track_id -> (row, col)
        
    def assign_seats(self, tracked_persons, width, height):
        """
        Assigns each tracked person to a unique seat grid (row, col) based on screen coordinates.
        Uses a collision-free mapping algorithm to ensure unique placement.
        """
        if not tracked_persons:
            self.seat_mapping = {}
            return {}
            
        # Sort tracked persons by y2 (bottom coordinate) descending (closest to camera first)
        sorted_persons = sorted(tracked_persons, key=lambda p: p['bbox'][3], reverse=True)
        
        assigned = {}
        occupied = set()
        
        for p in sorted_persons:
            track_id = p['track_id']
            x1, y1, x2, y2 = p['bbox']
            
            # Use bottom center coordinates for seating mapping
            x_bc = (x1 + x2) / 2.0
            y_bc = y2
            
            # Map X linearly to column index
            col = int(x_bc / (width / self.cols))
            col = max(0, min(self.cols - 1, col))
            
            # Map Y to row index (Row 0 is closest to bottom, Row 6 is closest to top)
            row = int((height - y_bc) / (height / self.rows))
            row = max(0, min(self.rows - 1, row))
            
            assigned_row, assigned_col = row, col
            
            # Collision resolution: if the target seat is already occupied, find the closest free seat
            if (assigned_row, assigned_col) in occupied:
                found = False
                # 1. Search other rows in the same column
                for r_offset in [1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 6, -6]:
                    test_row = row + r_offset
                    if 0 <= test_row < self.rows:
                        if (test_row, col) not in occupied:
                            assigned_row = test_row
                            found = True
                            break
                            
                # 2. If still occupied, search neighboring columns
                if not found:
                    for c_offset in [1, -1, 2, -2, 3, -3]:
                        test_col = col + c_offset
                        if 0 <= test_col < self.cols:
                            # Try matching rows in this column
                            for r in range(self.rows):
                                if (r, test_col) not in occupied:
                                    assigned_row = r
                                    assigned_col = test_col
                                    found = True
                                    break
                            if found:
                                break
            
            assigned[track_id] = (assigned_row, assigned_col)
            occupied.add((assigned_row, assigned_col))
            
        self.seat_mapping = assigned
        return assigned
