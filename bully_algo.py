import multiprocessing
import time
import random
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class Processor:
    def __init__(self, processor_id, priority, heartbeat_interval=3, heartbeat_timeout=10):
        self.processor_id = processor_id
        self.priority = priority
        self.leader = None
        self.backup_leader = None  # Added backup leader
        self.processors = {}
        self.failed = False
        self.heartbeat_interval = heartbeat_interval
        self.missed_heartbeats = 0
        self.heartbeat_timeout = heartbeat_timeout
        self.heartbeat_process = None
        self.acknowledged = multiprocessing.Value('i', 1)  # Shared flag for acknowledgment
        self.lock = multiprocessing.Lock()  # Add a lock for process safety

    def send_heartbeat(self):
        try:
            while not self.failed:
                start_time = time.time()
                time.sleep(self.heartbeat_interval)
                with self.lock:
                    if not self.failed:
                        if self.leader is None:
                            self.initiate_election()  # If no leader, initiate election
                        logger.info(f"Processor {self.processor_id} sends heartbeat. Leader: {self.leader}, Backup Leader: {self.backup_leader}")  # Print backup leader
                        self.detect_failure(start_time)
        except Exception as e:
            logger.error(f"Error in send_heartbeat for Processor {self.processor_id}: {e}")

    def start_heartbeat(self):
        try:
            if not self.heartbeat_process or not self.heartbeat_process.is_alive():
                self.heartbeat_process = multiprocessing.Process(target=self.send_heartbeat)
                self.heartbeat_process.daemon = True
                self.heartbeat_process.start()
            else:
                logger.info("Heartbeat process is already running.")
        except Exception as e:
            logger.error(f"Error in start_heartbeat for Processor {self.processor_id}: {e}")

    def stop_heartbeat(self):
        self.failed = True
        if self.heartbeat_process and self.heartbeat_process.is_alive():
            self.heartbeat_process.terminate()  # Terminate the heartbeat process
            self.heartbeat_process.join()  # Wait for the process to terminate
            logger.info(f"Heartbeat process for Processor {self.processor_id} stopped.")

    def detect_failure(self, start_time):
        current_time = time.time()
        elapsed_time = current_time - start_time
        with self.lock:
            if elapsed_time > self.heartbeat_timeout:
                logger.warning(f"Processor {self.processor_id} detected failure of Processor {self.leader}. Leader: {self.leader}, Backup Leader: {self.backup_leader}")  # Print backup leader
                self.leader = None
                self.backup_leader = None  # Reset backup leader if leader fails
                self.initiate_election()
            else:
                if self.missed_heartbeats >= 3:
                    logger.warning(f"Processor {self.processor_id} missed heartbeats. Possible failure. Leader: {self.leader}, Backup Leader: {self.backup_leader}")  # Print backup leader
                    self.failed = True  # Mark processor as failed
                    self.recover_network()
                    return  # Exit the method if the failure condition is met
                else:
                    self.missed_heartbeats += 1

    def initiate_election(self):
     higher_priority_processors = [processor_id for processor_id, processor in self.processors.items() if
                                  processor.priority > self.priority]
     if not higher_priority_processors:
        self.leader = self.processor_id  # Set itself as leader if no higher priority processors exist
        logger.info(f"Processor {self.processor_id} becomes the leader.")
        if self.backup_leader is None:
            self.backup_leader = self.processor_id  # Set itself as backup leader if there was none previously
        self.broadcast_leader()
     else:
        for processor_id in higher_priority_processors:
            if processor_id > self.processor_id:
                logger.info(f"Processor {self.processor_id} starts election process. Leader: {self.leader}, Backup Leader: {self.backup_leader}")  # Print backup leader
                self.send_election_message(processor_id)



    def send_election_message(self, processor_id):
        try:
            if processor_id in self.processors:
                self.processors[processor_id].respond_to_election(self.processor_id)
                logger.info(f"Leader: {self.leader}, Backup Leader: {self.backup_leader}")  # Print backup leader
            else:
                logger.error(f"Processor {processor_id} not found.")
        except Exception as e:
            logger.error(f"Error in send_election_message for Processor {self.processor_id}: {e}")

    def respond_to_election(self, initiating_processor_id):
        logger.info(f"Processor {self.processor_id} responds to Processor {initiating_processor_id}. Leader: {self.leader}, Backup Leader: {self.backup_leader}")  # Print backup leader
        with self.lock:
            if self.leader is None or self.priority > self.processors[initiating_processor_id].priority:
                self.leader = self.processor_id
                logger.info(f"Leader: {self.leader}, Backup Leader: {self.backup_leader}")  # Print backup leader
                self.broadcast_leader()

    def broadcast_leader(self):
        for processor_id in self.processors:
            self.processors[processor_id].update_leader(self.leader)

    def update_leader(self, new_leader):
        self.leader = new_leader
        logger.info(f"Processor {self.processor_id} updates leader to Processor {self.leader}, Backup Leader: {self.backup_leader}")  # Print backup leader
        if self.backup_leader is None:
            self.backup_leader = new_leader  # Update backup leader if there was none previously

    def join_network(self, processors):
        self.leader = random.choice(list(processors.keys()))
        logger.info(f"Processor {self.processor_id} joins the network with leader {self.leader}. Backup Leader: {self.backup_leader}")  # Print backup leader
        for processor_id, processor in processors.items():
            processor.processors[self.processor_id] = self
            self.processors[processor_id] = processor

    def recover_processor(self, processors, recovered_processor_id):
        try:
            if recovered_processor_id in processors:
                processor = processors[recovered_processor_id]
                with processor.lock:
                    processor.failed = False  # Mark processor as recovered
                    if max((p.processor_id for p in processors.values() if not p.failed), default=0) == processor.processor_id:
                        processor.initiate_election()  # Initiate election if the recovered processor was the last active one
                        processor.start_heartbeat()  # Restart heartbeat
                        logger.info(f"Processor {recovered_processor_id} recovered and restarted. Backup Leader: {self.backup_leader}")  # Print backup leader
            else:
                logger.error(f"Processor {recovered_processor_id} not found.")
        except Exception as e:
            logger.error(f"Error in recover_processor for Processor {self.processor_id}: {e}")

    def leave_network(self, processors):
     try:
        with self.lock:
            # Notify other processors to remove this processor
            for processor_id, processor in processors.items():
                with processor.lock:
                    if self.processor_id in processor.processors:
                        del processor.processors[self.processor_id]

            self.stop_heartbeat()  # Stop the heartbeat process before leaving the network
            del processors[self.processor_id]  # Remove the leaving processor from the processors dictionary
            logger.info(f"Processor {self.processor_id} leaves the network gracefully.")
     except Exception as e:
        logger.error(f"Error in leave_network for Processor {self.processor_id}: {e}")


    def update_configuration(self, heartbeat_interval=None, heartbeat_timeout=None, priority=None):
        with self.lock:
            if heartbeat_interval is not None:
                self.heartbeat_interval = heartbeat_interval
            if heartbeat_timeout is not None:
                self.heartbeat_timeout = heartbeat_timeout
            if priority is not None:
                self.priority = priority
            logger.info(f"Configuration updated for Processor {self.processor_id}: "
                        f"Heartbeat Interval: {self.heartbeat_interval}, "
                        f"Heartbeat Timeout: {self.heartbeat_timeout}, "
                        f"Priority: {self.priority}, Backup Leader: {self.backup_leader}")  # Print backup leader

    def recover_network(self):
        try:
            # Attempt to recover failed processors
            for processor_id, processor in self.processors.items():
                if processor_id != self.processor_id and processor.failed:
                    self.recover_processor(self.processors, processor_id)
        except Exception as e:
            logger.error(f"Error in recover_network for Processor {self.processor_id}: {e}")


def main():
    try:
        num_processors = 10
        processors = {processor_id: Processor(processor_id, random.randint(1, 10)) for processor_id in
                      range(1, num_processors + 1)}

        for processor in processors.values():
            processor.start_heartbeat()

        for processor in processors.values():
            processor.initiate_election()

        # Example of dynamic configuration update
        processor_id_to_update = 3
        heartbeat_interval = 5
        processors[processor_id_to_update].update_configuration(heartbeat_interval=heartbeat_interval)

        time.sleep(15)  # Let the system run for a while

        left_processor_id = 3
        if left_processor_id in processors:
         
         processors[left_processor_id].leave_network(processors)
        else:
         logger.error(f"Processor {left_processor_id} does not exist.")


    except Exception as e:
        logger.error("An error occurred:", e)


if __name__ == "__main__":
    main()
