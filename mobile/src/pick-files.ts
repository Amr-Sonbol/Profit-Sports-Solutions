import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';

import { alert } from '@/i18n';

/** A file picked on the phone, waiting to be sent. */
export interface PickedFile {
  uri: string;
  name: string;
  mimeType: string;
}

/** At most 10 files with a message, as on the website. */
export const MAX_FILES = 10;

/** Photos or videos from the gallery, several at once. */
export async function pickPhotos(limit = MAX_FILES): Promise<PickedFile[]> {
  const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!permission.granted) {
    alert('Permission required', 'Access to the photo library is required to attach a photo.');
    return [];
  }
  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ['images', 'videos'], quality: 0.8, allowsMultipleSelection: true, selectionLimit: limit,
  });
  if (result.canceled) {
    return [];
  }
  return result.assets.slice(0, limit).map((asset) => ({
    uri: asset.uri, name: asset.fileName ?? 'photo.jpg', mimeType: asset.mimeType ?? 'image/jpeg',
  }));
}

/** PDFs (or other photos) from the phone's files. */
export async function pickDocuments(limit = MAX_FILES): Promise<PickedFile[]> {
  const result = await DocumentPicker.getDocumentAsync({
    type: ['application/pdf', 'image/*', 'video/*'], copyToCacheDirectory: true, multiple: true,
  });
  if (result.canceled) {
    return [];
  }
  return result.assets.slice(0, limit).map((asset) => ({
    uri: asset.uri, name: asset.name, mimeType: asset.mimeType ?? 'application/pdf',
  }));
}
