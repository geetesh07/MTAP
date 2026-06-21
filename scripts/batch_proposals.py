"""
batch_proposals.py — dev wrapper around app.dxf.proposal_batch.generate_matrix.
Generates the proposal-DXF matrix into Desktop/MTAP_Proposals.
The shipped exe does the same via:  MTAP.exe --gen-proposals <dir>
"""

import os

from app.dxf.proposal_batch import generate_matrix

if __name__ == "__main__":
    root = os.path.join(os.path.expanduser("~"), "Desktop", "MTAP_Proposals")
    generate_matrix(root)
