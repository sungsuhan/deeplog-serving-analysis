"""DeepLog model definition placeholder."""

import torch
import torch.nn as nn


class DeepLog(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, num_layers: int, num_classes: int):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_size, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        out = self.fc(out[:, -1, :])
        return out
