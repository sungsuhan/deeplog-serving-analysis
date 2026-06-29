"""
DeepLog 모델 정의 (Du et al. 2017, "DeepLog: Anomaly Detection and Diagnosis
from System Logs through Deep Learning").

핵심 아이디어:
  시스템 로그에서 추출한 로그 키(log key) 시퀀스를 LSTM에 학습시켜,
  "정상 시스템에서 다음에 나타날 로그 키"를 예측하도록 훈련한다.
  추론 시 실제 다음 키가 상위 g개 예측 안에 없으면 이상(anomaly)으로 판단.
"""

import torch
import torch.nn as nn


class DeepLog(nn.Module):
    """
    LSTM 기반 로그 이상탐지 모델.

    입력: one-hot 인코딩된 로그 키 시퀀스  (batch, window_size-1, num_classes)
    출력: 다음 로그 키에 대한 클래스별 로짓  (batch, num_classes)

    input_size == num_classes 인 이유:
      one-hot 벡터의 차원이 vocab 크기와 같기 때문.
      임베딩 레이어를 쓰지 않는 것은 원 논문의 구현을 따른 것이며,
      vocab이 크지 않은(100개 수준) 로그 도메인에서는 one-hot으로 충분하다.
    """

    def __init__(self, input_size: int, hidden_size: int,
                 num_layers: int, num_classes: int):
        super().__init__()
        # num_layers=2: 논문 기본값. 레이어를 더 쌓으면 장기 패턴 포착 능력이 높아지지만
        # 로그 시퀀스처럼 짧고 규칙적인 패턴에선 2층으로 충분하다.
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.fc   = nn.Linear(hidden_size, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        # 마지막 타임스텝의 hidden state만 분류에 사용.
        # 시퀀스 전체를 본 뒤 "다음 키"를 예측하는 구조.
        out = self.fc(out[:, -1, :])
        return out  # 로짓 반환 (softmax는 호출부에서 적용)
