import os
import shutil
import logging
from pathlib import Path
from typing import BinaryIO, Optional, Generator
from datetime import datetime
import uuid

# Configure logging
logger = logging.getLogger(__name__)

# Configuration - default to local storage in ./storage directory
STORAGE_BASE_PATH = os.getenv("STORAGE_PATH", "./storage")


class LocalStorageService:
    """
    Local filesystem storage for segmentation files.
    Optimized for low-budget deployments with automatic cleanup and disk management.
    """
    
    def __init__(self, base_path: str = STORAGE_BASE_PATH):
        self.base_path = Path(base_path).resolve()
        self._ensure_directories()
        logger.info(f"LocalStorageService initialized at: {self.base_path}")
    
    def _ensure_directories(self):
        """Create storage directory structure if it doesn't exist"""
        directories = [
            'segmentations',  # Full .nrrd files
            'deltas',         # Incremental changes
            'snapshots',      # Periodic snapshots during live sessions
            'temp',           # Temporary uploads
            'versions'        # Version-specific files
        ]
        for dir_name in directories:
            dir_path = self.base_path / dir_name
            dir_path.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured directory exists: {dir_path}")
    
    def _generate_filename(self, file_type: str, segmentation_id: int, 
                          version: Optional[int] = None) -> str:
        """
        Generate unique filename with meaningful naming convention
        
        Format: seg_{id}_v{version}_{type}_{timestamp}_{uuid}.ext
        Example: seg_123_v5_nrrd_20260111_143025_a1b2c3d4.nrrd
        
        Args:
            file_type: Type of file ('nrrd', 'delta', 'snapshot')
            segmentation_id: ID of the segmentation
            version: Optional version number
            
        Returns:
            str: Generated filename
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        unique_id = uuid.uuid4().hex[:8]
        
        # Map file types to extensions
        extensions = {
            'nrrd': 'nrrd',
            'delta': 'json',
            'snapshot': 'nrrd',
            'version': 'nrrd'
        }
        ext = extensions.get(file_type, 'dat')
        
        # Include version if provided
        version_str = f"_v{version}" if version is not None else ""
        
        return f"seg_{segmentation_id}{version_str}_{file_type}_{timestamp}_{unique_id}.{ext}"
    
    def save_file(self, file_data: BinaryIO, file_type: str, 
                  segmentation_id: int, version: Optional[int] = None,
                  metadata: dict = None) -> str:
        """
        Save file to local filesystem
        
        Args:
            file_data: Binary file data (file-like object)
            file_type: Type of file ('nrrd', 'delta', 'snapshot', 'version')
            segmentation_id: ID of segmentation
            version: Optional version number for versioned files
            metadata: Optional metadata (currently for logging only)
            
        Returns:
            str: Relative file path (stored in database)
        """
        # Determine subdirectory based on file type
        subdir_map = {
            'nrrd': 'segmentations',
            'delta': 'deltas',
            'snapshot': 'snapshots',
            'version': 'versions'
        }
        subdir = subdir_map.get(file_type, 'temp')
        
        # Generate unique filename
        filename = self._generate_filename(file_type, segmentation_id, version)
        full_path = self.base_path / subdir / filename
        
        # Save file with error handling
        try:
            with open(full_path, 'wb') as f:
                shutil.copyfileobj(file_data, f)
            
            file_size = full_path.stat().st_size
            logger.info(f"Saved file: {filename} ({file_size} bytes)")
            
            if metadata:
                logger.debug(f"File metadata: {metadata}")
            
        except Exception as e:
            logger.error(f"Failed to save file {filename}: {e}")
            raise
        
        # Return relative path for database storage
        return f"{subdir}/{filename}"
    
    def get_file(self, file_path: str) -> bytes:
        """
        Read entire file from local filesystem
        
        Args:
            file_path: Relative path to file
            
        Returns:
            bytes: File content
            
        Raises:
            FileNotFoundError: If file doesn't exist
        """
        full_path = self.base_path / file_path
        
        if not full_path.exists():
            logger.error(f"File not found: {file_path}")
            raise FileNotFoundError(f"File not found: {file_path}")
        
        try:
            with open(full_path, 'rb') as f:
                content = f.read()
            logger.debug(f"Read file: {file_path} ({len(content)} bytes)")
            return content
        except Exception as e:
            logger.error(f"Failed to read file {file_path}: {e}")
            raise
    
    def get_file_stream(self, file_path: str, chunk_size: int = 8192) -> Generator[bytes, None, None]:
        """
        Stream file in chunks for efficient memory usage with large files
        
        Args:
            file_path: Relative path to file
            chunk_size: Size of each chunk in bytes (default 8KB)
            
        Yields:
            bytes: File chunks
        """
        full_path = self.base_path / file_path
        
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        try:
            with open(full_path, 'rb') as f:
                while chunk := f.read(chunk_size):
                    yield chunk
        except Exception as e:
            logger.error(f"Failed to stream file {file_path}: {e}")
            raise
    
    def delete_file(self, file_path: str) -> bool:
        """
        Delete file from local filesystem
        
        Args:
            file_path: Relative path to file
            
        Returns:
            bool: True if file was deleted, False if it didn't exist
        """
        full_path = self.base_path / file_path
        
        try:
            if full_path.exists():
                full_path.unlink()
                logger.info(f"Deleted file: {file_path}")
                return True
            else:
                logger.warning(f"File not found for deletion: {file_path}")
                return False
        except Exception as e:
            logger.error(f"Error deleting file {file_path}: {e}")
            return False
    
    def file_exists(self, file_path: str) -> bool:
        """
        Check if file exists
        
        Args:
            file_path: Relative path to file
            
        Returns:
            bool: True if file exists
        """
        full_path = self.base_path / file_path
        return full_path.exists()
    
    def get_full_path(self, file_path: str) -> Path:
        """
        Get absolute filesystem path for a relative path
        
        Args:
            file_path: Relative path
            
        Returns:
            Path: Absolute path
        """
        return self.base_path / file_path
    
    def get_file_size(self, file_path: str) -> int:
        """
        Get file size in bytes
        
        Args:
            file_path: Relative path to file
            
        Returns:
            int: File size in bytes
            
        Raises:
            FileNotFoundError: If file doesn't exist
        """
        full_path = self.base_path / file_path
        
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        return full_path.stat().st_size
    
    def get_storage_stats(self) -> dict:
        """
        Get storage statistics for monitoring disk usage
        
        Returns:
            dict: Storage statistics including total size and file counts
        """
        stats = {
            'total_size': 0,
            'file_count': 0,
            'by_type': {}
        }
        
        for subdir in ['segmentations', 'deltas', 'snapshots', 'versions', 'temp']:
            dir_path = self.base_path / subdir
            if not dir_path.exists():
                continue
            
            dir_size = 0
            file_count = 0
            
            for file_path in dir_path.rglob('*'):
                if file_path.is_file():
                    size = file_path.stat().st_size
                    dir_size += size
                    file_count += 1
            
            stats['by_type'][subdir] = {
                'size': dir_size,
                'count': file_count
            }
            stats['total_size'] += dir_size
            stats['file_count'] += file_count
        
        logger.info(f"Storage stats: {stats['file_count']} files, "
                   f"{stats['total_size'] / (1024**3):.2f} GB")
        
        return stats
    
    def cleanup_temp_files(self, max_age_hours: int = 24) -> int:
        """
        Clean up old temporary files
        
        Args:
            max_age_hours: Delete temp files older than this many hours
            
        Returns:
            int: Number of files deleted
        """
        temp_dir = self.base_path / 'temp'
        if not temp_dir.exists():
            return 0
        
        deleted_count = 0
        cutoff_time = datetime.utcnow().timestamp() - (max_age_hours * 3600)
        
        for file_path in temp_dir.iterdir():
            if file_path.is_file():
                if file_path.stat().st_mtime < cutoff_time:
                    try:
                        file_path.unlink()
                        deleted_count += 1
                        logger.debug(f"Cleaned up temp file: {file_path.name}")
                    except Exception as e:
                        logger.error(f"Failed to delete temp file {file_path}: {e}")
        
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} temporary files")
        
        return deleted_count


# Singleton instance for easy importing
_storage_instance = None

def get_storage_service() -> LocalStorageService:
    """
    Get the singleton storage service instance
    
    Returns:
        LocalStorageService: The storage service instance
    """
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = LocalStorageService()
    return _storage_instance


# Usage examples
if __name__ == "__main__":
    # Configure logging for examples
    logging.basicConfig(level=logging.INFO)
    
    # Get storage service
    storage = get_storage_service()
    
    print(f"Storage initialized at: {storage.base_path}\n")
    
    # Example 1: Save a file
    print("Example 1: Saving a file")
    from io import BytesIO
    test_data = b"This is test segmentation data" * 1000
    file_obj = BytesIO(test_data)
    
    file_path = storage.save_file(
        file_data=file_obj,
        file_type="nrrd",
        segmentation_id=123,
        version=1,
        metadata={"created_by": "user_5", "size": len(test_data)}
    )
    print(f"✓ File saved to: {file_path}\n")
    
    # Example 2: Retrieve a file
    print("Example 2: Retrieving a file")
    file_content = storage.get_file(file_path)
    print(f"✓ Retrieved {len(file_content)} bytes\n")
    
    # Example 3: Check if file exists
    print("Example 3: Checking file existence")
    exists = storage.file_exists(file_path)
    print(f"✓ File exists: {exists}\n")
    
    # Example 4: Get file size
    print("Example 4: Getting file size")
    size = storage.get_file_size(file_path)
    print(f"✓ File size: {size} bytes\n")
    
    # Example 5: Stream large file
    print("Example 5: Streaming file in chunks")
    chunk_count = 0
    for chunk in storage.get_file_stream(file_path, chunk_size=1024):
        chunk_count += 1
    print(f"✓ Streamed file in {chunk_count} chunks\n")
    
    # Example 6: Get storage statistics
    print("Example 6: Storage statistics")
    stats = storage.get_storage_stats()
    print(f"✓ Total files: {stats['file_count']}")
    print(f"✓ Total size: {stats['total_size'] / 1024:.2f} KB")
    for file_type, type_stats in stats['by_type'].items():
        print(f"  - {file_type}: {type_stats['count']} files, "
              f"{type_stats['size'] / 1024:.2f} KB")
    print()
    
    # Example 7: Delete a file
    print("Example 7: Deleting a file")
    success = storage.delete_file(file_path)
    print(f"✓ File deleted: {success}\n")
    
    # Example 8: Cleanup temp files
    print("Example 8: Cleanup temporary files")
    cleaned = storage.cleanup_temp_files(max_age_hours=24)
    print(f"✓ Cleaned up {cleaned} temporary files")
