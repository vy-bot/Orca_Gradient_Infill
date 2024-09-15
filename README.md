# Orca_Gradient_Infill
Orca gradient infill post processing script.

Features:
    - Accepts the G-code file path as the only argument.
    - Processes the G-code file in place.
    - Uses a configuration file (cfg) to store default values.
    - The cfg file is located in the same directory as the script/executable.
    - Accounts for running as a script or as a compiled executable (e.g., using PyInstaller).
    - Reads values from the cfg file and uses them for processing.
    - Provides warnings if G2/G3 commands are used within the INFILL section and if relative extrusion is not set.
    - Accurately detects and leverages the infill type from the G-code file by parsing `sparse_infill_pattern`.
    - Prints the settings used from the cfg file.
    - Writes a log file with processing information and statistics, including input/output file names and settings used.
    - The log file is saved in the same directory as the script.
    
