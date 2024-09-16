#!/usr/bin/env python3

import sys
import re
import os
import datetime
import configparser
from collections import namedtuple
from enum import Enum
from typing import List, Tuple, Dict, Any
import traceback
import time

__version__ = '2.0'

"""
Features:
    - Accepts the G-code file path as the only argument.
    - Processes the G-code file in place.
    - Uses a configuration file (cfg) to store default values.
    - The cfg file is located in the same directory as the script/executable.
    - Accounts for running as a script or as a compiled executable (e.g., using PyInstaller).
    - Reads values from the cfg file and uses them for processing.
    - Provides warnings if G2/G3 commands are used within the INFILL section and if relative extrusion is not set.
    - Accurately detects and leverages the infill type from the G-code file by parsing `sparse_infill_pattern`.
    - Prevents duplicate G1 F commands.
    - Prints the settings used from the cfg file.
    - Writes a log file with processing information and statistics, including input/output file names and settings used.
    - The log file is saved in the same directory as the script.
"""

class InfillType(Enum):
    """Enum for infill type."""
    SMALL_SEGMENTS = 1  # Infill with small segments like gyroid or honeycomb
    LINEAR = 2          # Linear infill like rectilinear or triangles

Point2D = namedtuple('Point2D', 'x y')
Segment = namedtuple('Segment', 'point1 point2')

# Determine the application path (directory where the script or executable is located)
if getattr(sys, 'frozen', False):
    # If the script is compiled with PyInstaller
    application_path = os.path.dirname(sys.executable)
else:
    # If the script is running as a script
    application_path = os.path.dirname(os.path.abspath(__file__))

# Configuration file path
CONFIG_FILE_NAME = 'script_config.cfg'
CONFIG_FILE_PATH = os.path.join(application_path, CONFIG_FILE_NAME)

# Default values for settings (will be loaded from cfg)
DEFAULT_MAX_FLOW = 250.0  # Maximum extrusion flow percentage
DEFAULT_MIN_FLOW = 50.0   # Minimum extrusion flow percentage
DEFAULT_GRADIENT_THICKNESS = 6.0  # Thickness of the gradient (max to min) in mm
DEFAULT_GRADIENT_DISCRETIZATION = 4.0  # Number of segments within the gradient (only for linear infill)

class Section(Enum):
    """Enum for section type."""
    NOTHING = 0
    INNER_WALL = 1
    INFILL = 2

def dist(segment: Segment, point: Point2D) -> float:
    """Calculate the distance from a point to a line with finite length."""
    px = segment.point2.x - segment.point1.x
    py = segment.point2.y - segment.point1.y
    norm = px * px + py * py
    try:
        u = ((point.x - segment.point1.x) * px + (point.y - segment.point1.y) * py) / float(norm)
    except ZeroDivisionError:
        return 0

    u = max(min(u, 1), 0)
    x = segment.point1.x + u * px
    y = segment.point1.y + u * py
    dx = x - point.x
    dy = y - point.y

    return (dx * dx + dy * dy) ** 0.5

def get_points_distance(point1: Point2D, point2: Point2D) -> float:
    """Calculate the Euclidean distance between two points."""
    return ((point1.x - point2.x) ** 2 + (point1.y - point2.y) ** 2) ** 0.5

def min_distance_from_segment(segment: Segment, segments: List[Segment]) -> float:
    """Calculate the minimum distance from the midpoint of 'segment' to the nearest segment in 'segments'."""
    middlePoint = Point2D((segment.point1.x + segment.point2.x) / 2, (segment.point1.y + segment.point2.y) / 2)
    return min(dist(s, middlePoint) for s in segments) if segments else float('inf')

# Pre-compile regex patterns
prog_searchX = re.compile(r"X(-?\d*\.?\d*)")
prog_searchY = re.compile(r"Y(-?\d*\.?\d*)")

def getXY(currentLine: str) -> Point2D:
    """Create a 'Point2D' object from a G-code line."""
    searchX = prog_searchX.search(currentLine)
    searchY = prog_searchY.search(currentLine)

    if searchX and searchY:
        elementX = searchX.group(1)
        elementY = searchY.group(1)
    else:
        raise SyntaxError(f'G-code file parsing error for line: {currentLine}')

    return Point2D(float(elementX), float(elementY))

def mapRange(a: Tuple[float, float], b: Tuple[float, float], s: float) -> float:
    """Calculate a multiplier for the extrusion value from the distance to the perimeter."""
    (a1, a2), (b1, b2) = a, b
    if a2 - a1 == 0:
        return b1  # Avoid division by zero
    return b1 + ((s - a1) * (b2 - b1) / (a2 - a1))

def get_extrusion_command(x: float, y: float, extrusion: float, feedrate: float) -> str:
    """Format a G-code string from the X, Y coordinates and extrusion value."""
    if feedrate>0:
        return "G1 X{} Y{} E{} F{}\n".format(round(x, 3), round(y, 3), round(extrusion, 5), round(feedrate, 3))
    else:
        return "G1 X{} Y{} E{}\n".format(round(x, 3), round(y, 3), round(extrusion, 5))

def is_begin_layer_line(line: str) -> bool:
    """Check if current line is the start of a layer section."""
    return line.startswith(";LAYER_CHANGE") or line.startswith(";LAYER:")

def is_begin_inner_wall_line(line: str) -> bool:
    """Check if current line is the start of an inner wall section."""
    return line.startswith(";TYPE:Inner wall")

def is_end_inner_wall_line(line: str) -> bool:
    """Check if current line is the start of an outer wall section."""
    return line.startswith(";TYPE:Outer wall") or line.startswith(";TYPE:Solid infill") or line.startswith(";TYPE:Skin")

def is_extrusion_line(line: str) -> bool:
    """Check if current line is a standard printing segment."""
    return "G1" in line and " X" in line and "Y" in line and "E" in line

def is_begin_infill_segment_line(line: str) -> bool:
    """Check if current line is the start of an infill."""
    return line.startswith(";TYPE:Sparse infill") or line.startswith(";TYPE:Infill")

def extract_infill_type(gcode_lines: List[str]) -> InfillType:
    """Extract the infill type from the G-code file."""
    sparse_infill_pattern = None
    sparse_infill_pattern_pattern = re.compile(r"^; sparse_infill_pattern = (.+)")
    for line in gcode_lines:
        match = sparse_infill_pattern_pattern.match(line)
        if match:
            sparse_infill_pattern = match.group(1).strip().lower()
            break

    if sparse_infill_pattern:
        if sparse_infill_pattern in ["gyroid", "honeycomb", "adaptivecubic", "cubic", "tetrahedral"]:
            return InfillType.SMALL_SEGMENTS
        else:
            return InfillType.LINEAR
    else:
        # Default to LINEAR if not found
        return InfillType.LINEAR

def read_config(config_file_path: str) -> Dict[str, float]:
    """Read configuration parameters from the cfg file."""
    config = configparser.ConfigParser()
    if not os.path.isfile(config_file_path):
        # Create cfg file with default values
        config['DEFAULT'] = {
            'MAX_FLOW': str(DEFAULT_MAX_FLOW),
            'MIN_FLOW': str(DEFAULT_MIN_FLOW),
            'GRADIENT_THICKNESS': str(DEFAULT_GRADIENT_THICKNESS),
            'GRADIENT_DISCRETIZATION': str(DEFAULT_GRADIENT_DISCRETIZATION)
        }
        with open(config_file_path, 'w') as cfgfile:
            config.write(cfgfile)
        print(f"Configuration file '{config_file_path}' created with default values.")
    else:
        config.read(config_file_path)
        # Check if all required parameters are present
        missing_params = []
        for param in ['MAX_FLOW', 'MIN_FLOW', 'GRADIENT_THICKNESS', 'GRADIENT_DISCRETIZATION']:
            if not config.has_option('DEFAULT', param):
                missing_params.append(param)
        if missing_params:
            # Update cfg file with missing default values
            for param in missing_params:
                default_value = str(globals()[f'DEFAULT_{param}'])
                config.set('DEFAULT', param, default_value)
            with open(config_file_path, 'w') as cfgfile:
                config.write(cfgfile)
            print(f"Configuration file '{config_file_path}' updated with missing parameters: {', '.join(missing_params)}")

    # Read parameters from cfg file
    params = {
        'MAX_FLOW': config.getfloat('DEFAULT', 'MAX_FLOW'),
        'MIN_FLOW': config.getfloat('DEFAULT', 'MIN_FLOW'),
        'GRADIENT_THICKNESS': config.getfloat('DEFAULT', 'GRADIENT_THICKNESS'),
        'GRADIENT_DISCRETIZATION': config.getfloat('DEFAULT', 'GRADIENT_DISCRETIZATION')
    }
    return params

def reduce_by_percentage(value, percentage):
    return value / (percentage / 100)

def process_gcode_file(
    gcode_file_path: str,
    max_flow: float,
    min_flow: float,
    gradient_thickness: float,
    gradient_discretization: float,
) -> Dict[str, Any]:
    """Process the G-code file in place and modify infill portions with an extrusion width gradient."""
    # Pre-compile regex patterns
    prog_move = re.compile(r'^G[0-1].*X.*Y')
    prog_extrusion = re.compile(r'^G1.*X.*Y.*E')
    prog_type = re.compile(r'^;TYPE:')
    prog_g2_g3 = re.compile(r'^G[2-3]')
    prog_relative_extrusion = re.compile(r'^M83')
    prog_absolute_extrusion = re.compile(r'^M82')
    prog_g1_feedrate = re.compile(r'^G1.+F([\d\.]+)')

    lines = []
    edit = 0
    stats = {'total_lines': 0, 'modifications_made': 0}
    currentSection = Section.NOTHING
    lastPosition = Point2D(-10000, -10000)
    gradientDiscretizationLength = gradient_thickness / gradient_discretization
    relative_extrusion = False
    g2_g3_used = False
    g2_g3_lines = []
    relative_extrusion_set = False
    g1_feedrate = 0
    perimeterSegments = []
    infill_type = None  # Will be set after extracting from G-code

    # Read all lines from the G-code file
    with open(gcode_file_path, "r") as gcodeFile:
        gcode_lines = gcodeFile.readlines()

    stats['total_lines'] = len(gcode_lines)

    # Extract infill type from G-code file
    infill_type = extract_infill_type(gcode_lines)
    if infill_type == InfillType.SMALL_SEGMENTS:
        print("Detected infill type: SMALL_SEGMENTS")
    else:
        print("Detected infill type: LINEAR")

    for currentLine in gcode_lines:
        writtenToFile = False

        #keep tack of extrusion mode
        if prog_relative_extrusion.search(currentLine):
            relative_extrusion_set = True
        elif prog_absolute_extrusion.search(currentLine):
            relative_extrusion_set = False



        # Search if it indicates a type
        if prog_type.search(currentLine):
            if is_begin_inner_wall_line(currentLine):
                currentSection = Section.INNER_WALL
            elif is_end_inner_wall_line(currentLine):
                currentSection = Section.NOTHING
            elif is_begin_infill_segment_line(currentLine):
                currentSection = Section.INFILL
                g1_feedrate = 0
            else:
                currentSection = Section.NOTHING

        if currentSection == Section.INNER_WALL and is_extrusion_line(currentLine):
            perimeterSegments.append(Segment(getXY(currentLine), lastPosition))

        if currentSection == Section.INFILL:
            # check extrusion mode
            if not relative_extrusion_set:
                print("!!!ERROR!!! Please don't use relative extrusion on infill")
                print("!!!ERROR!!! Please don't use relative extrusion on infill")
                print("!!!ERROR!!! Please don't use relative extrusion on infill")
                exit(1)


            # Check for G2/G3 commands **only in the infill section**
            if prog_g2_g3.search(currentLine):
                g2_g3_used = True
                g2_g3_lines.append(currentLine.strip())

            prog_g1_feedrate_match = prog_g1_feedrate.match(currentLine)
            if prog_g1_feedrate_match:
                g1_feedrate = float(prog_g1_feedrate_match.group(1))

            if prog_extrusion.search(currentLine):
                currentPosition = getXY(currentLine)
                splitLine = currentLine.strip().split(" ")

                if infill_type == InfillType.LINEAR:
                    # Find extrusion length
                    extrusionLength = None
                    for element in splitLine:
                        if "E" in element:
                            extrusionLength = float(element[1:])
                    if extrusionLength is None:
                        raise ValueError(f"No extrusion length found in line: {currentLine}")

                    segmentLength = get_points_distance(lastPosition, currentPosition)
                    if segmentLength == 0:
                        segmentSteps = 1
                    else:
                        segmentSteps = segmentLength / gradientDiscretizationLength
                    extrusionLengthPerSegment = extrusionLength / segmentSteps if segmentSteps != 0 else 0
                    segmentDirection = Point2D(
                        (currentPosition.x - lastPosition.x) / segmentSteps if segmentSteps != 0 else 0,
                        (currentPosition.y - lastPosition.y) / segmentSteps if segmentSteps != 0 else 0,
                    )
                    if segmentSteps >= 2:
                        for _ in range(int(segmentSteps)):
                            segmentEnd = Point2D(
                                lastPosition.x + segmentDirection.x, lastPosition.y + segmentDirection.y
                            )
                            shortestDistance = min_distance_from_segment(
                                Segment(lastPosition, segmentEnd), perimeterSegments
                            )
                            extrusion_ratio = mapRange(
                                (0, gradient_thickness), 
                                (max_flow / 100, min_flow / 100), 
                                min(shortestDistance, gradient_thickness)
                            )
                            segmentExtrusion = extrusionLengthPerSegment * extrusion_ratio
                            feedrate = reduce_by_percentage(g1_feedrate, extrusion_ratio*100)

                            lines.append(get_extrusion_command(segmentEnd.x, segmentEnd.y, segmentExtrusion, feedrate))
                            lastPosition = segmentEnd
                        # Missing Segment
                        segmentLengthRatio = get_points_distance(lastPosition, currentPosition) / segmentLength if segmentLength != 0 else 0
                        lines.append(
                            get_extrusion_command(
                                currentPosition.x,
                                currentPosition.y,
                                segmentLengthRatio * extrusionLength * max_flow / 100,
                                reduce_by_percentage(g1_feedrate, max_flow)
                            )
                        )
                    else:
                        outPutLine = ""
                        feedrate_set = False
                        for element in splitLine:
                            if "E" in element:
                                outPutLine += "E" + str(round(float(element[1:]) * max_flow / 100, 5)) + " "
                            else:
                                outPutLine += element + " "
                            if "F" in element:
                                feedrate_set = True
                        if not feedrate_set:
                            outPutLine += f"F{reduce_by_percentage(g1_feedrate, max_flow)}" + " "
                        outPutLine = outPutLine.strip() + "\n"
                        lines.append(outPutLine)
                    writtenToFile = True
                    edit += 1

                elif infill_type == InfillType.SMALL_SEGMENTS:
                    shortestDistance = min_distance_from_segment(
                        Segment(lastPosition, currentPosition), perimeterSegments
                    )

                    outPutLine = ""
                    feedrate_set = False
                    for element in splitLine:
                        if "E" in element:
                            if shortestDistance < gradient_thickness:
                                newE = float(element[1:]) * mapRange((0, gradient_thickness), (max_flow / 100, min_flow / 100), shortestDistance)
                            else:
                                newE = float(element[1:]) * min_flow / 100
                            outPutLine += "E" + str(round(newE, 5)) + " "
                        else:
                            outPutLine += element + " "
                        if "F" in element:
                                feedrate_set = True
                    if not feedrate_set:
                        if shortestDistance < gradient_thickness:
                            newF = g1_feedrate / mapRange((0, gradient_thickness), (max_flow / 100, min_flow / 100), shortestDistance)
                        outPutLine += f"F{round(newF,3)}" + " "
                    outPutLine = outPutLine.strip() + "\n"
                    lines.append(outPutLine)
                    writtenToFile = True
                    edit += 1

                lastPosition = currentPosition

            if prog_move.search(currentLine):
                lastPosition = getXY(currentLine)
                if not writtenToFile:
                    lines.append(currentLine)
                    writtenToFile = True

            if not writtenToFile:
                lines.append(currentLine)
                writtenToFile = True

        else:
            # Update last position if move command
            if prog_move.search(currentLine):
                lastPosition = getXY(currentLine)

            if not writtenToFile:
                lines.append(currentLine)
                writtenToFile = True

    stats['modifications_made'] = edit

    # After processing, check for warnings
    if not relative_extrusion:
        print("WARNING: The G-code uses absolute extrusion. This script requires relative extrusion (M83).")
        stats['relative_extrusion'] = False
    else:
        stats['relative_extrusion'] = True

    if g2_g3_used:
        print("WARNING: The G-code contains G2/G3 commands (arc movements) in the infill section, which may not be supported by this script.")
        stats['g2_g3_used'] = True
        stats['g2_g3_lines'] = g2_g3_lines
    else:
        stats['g2_g3_used'] = False

    if edit == 0:
        print('No changes were made to the file! Check the script and input parameters.')
        stats['changes_made'] = False
    else:
        stats['changes_made'] = True

    stats['infill_type'] = infill_type.name

    # Write the modified G-code back to the same file
    with open(gcode_file_path, "w") as outputFile:
        for line in lines:
            outputFile.write("%s" % line)

    return stats


import argparse
def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Process a G-code file and override configuration values.")
    parser.add_argument("gcode_file_path", help="Path to the G-code file to process")
    parser.add_argument("--max_flow", type=float, help="Override the max_flow value from the config file")
    parser.add_argument("--min_flow", type=float, help="Override the min_flow value from the config file")
    parser.add_argument("--gradient_thickness", type=float, help="Override the gradient_thickness value from the config file")
    parser.add_argument("--gradient_discretization", type=float, help="Override the gradient_discretization value from the config file")
    return parser.parse_args()

def main():
    try:
        # Parse command-line arguments
        args = parse_args()

        # Read configuration parameters from the config file
        cfg_params = read_config(CONFIG_FILE_PATH)
        max_flow = args.max_flow if args.max_flow is not None else cfg_params['MAX_FLOW']
        min_flow = args.min_flow if args.min_flow is not None else cfg_params['MIN_FLOW']
        gradient_thickness = args.gradient_thickness if args.gradient_thickness is not None else cfg_params['GRADIENT_THICKNESS']
        gradient_discretization = args.gradient_discretization if args.gradient_discretization is not None else cfg_params['GRADIENT_DISCRETIZATION']

        # Print the settings used
        print(f"Using the following settings:")
        print(f"  MAX_FLOW = {max_flow}")
        print(f"  MIN_FLOW = {min_flow}")
        print(f"  GRADIENT_THICKNESS = {gradient_thickness}")
        print(f"  GRADIENT_DISCRETIZATION = {gradient_discretization}")

        # Process the G-code file with the parameters (either from the config or overridden via command line)
        start = time.time()
        stats = process_gcode_file(
            args.gcode_file_path, max_flow, min_flow, gradient_thickness, gradient_discretization
        )
        processing_time = time.time() - start
        print('Time to execute:', processing_time)

        # Write log file (if applicable)
        log_file_name = os.path.splitext(os.path.basename(args.gcode_file_path))[0] + '.log'
        log_file_path = os.path.join(application_path, log_file_name)
        with open(log_file_path, 'w') as log_file:
            log_file.write(f"Processing Date and Time: {datetime.datetime.now()}\n")
            log_file.write(f"G-code File: {args.gcode_file_path}\n")
            log_file.write(f"Processing Time: {processing_time:.2f} seconds\n")
            log_file.write(f"Total Lines Processed: {stats['total_lines']}\n")
            log_file.write(f"Modifications Made: {stats['modifications_made']}\n")
            log_file.write(f"Changes Made to File: {'Yes' if stats['changes_made'] else 'No'}\n")
            log_file.write(f"Infill Type: {stats['infill_type']}\n")
            log_file.write(f"Relative Extrusion Used: {'Yes' if stats['relative_extrusion'] else 'No'}\n")
            log_file.write(f"G2/G3 Commands Used: {'Yes' if stats['g2_g3_used'] else 'No'}\n")
            if stats.get('g2_g3_used'):
                log_file.write("G2/G3 Lines:\n")
                for line in stats['g2_g3_lines']:
                    log_file.write(line + '\n')
            log_file.write("Settings Used:\n")
            log_file.write(f"  MAX_FLOW: {max_flow}\n")
            log_file.write(f"  MIN_FLOW: {min_flow}\n")
            log_file.write(f"  GRADIENT_THICKNESS: {gradient_thickness}\n")
            log_file.write(f"  GRADIENT_DISCRETIZATION: {gradient_discretization}\n")

        print(f"Log file written to: {log_file_path}")

    except Exception as e:
        traceback.print_exc()
        print('An error occurred during G-code processing.')
        sys.exit(1)

if __name__ == '__main__':
    main()
