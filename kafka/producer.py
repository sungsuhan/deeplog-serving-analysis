"""Kafka producer placeholder for streaming log events."""

from kafka import KafkaProducer


def create_producer(bootstrap_servers: str = "localhost:9092") -> KafkaProducer:
    return KafkaProducer(bootstrap_servers=bootstrap_servers)


def send_log_event(producer: KafkaProducer, topic: str, message: bytes) -> None:
    producer.send(topic, message)
    producer.flush()


if __name__ == "__main__":
    print("Kafka producer placeholder.")
